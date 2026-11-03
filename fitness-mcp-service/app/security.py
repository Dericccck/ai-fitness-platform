"""Per-request service authentication and independently verified business identity."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import anyio
from fitness_tool_contracts.agent_context import AgentContextVerifier, AgentIdentity
from fitness_tool_contracts.jwks import JwksPublicKeyProvider
from pydantic import TypeAdapter

from .config import ServiceCredential, Settings


class AuthenticationFailure(ValueError):
    pass


@dataclass(frozen=True)
class InvocationContext:
    identity: AgentIdentity
    signed_context: str
    principal: ServiceCredential
    request_id: str
    trace_id: str | None
    confirmation_token: str | None
    deadline: float


class Security:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.credentials = TypeAdapter(list[ServiceCredential]).validate_json(
            settings.service_credentials_json
        )
        if any(credential.resource != settings.resource_url for credential in self.credentials):
            raise ValueError("Service credentials must be bound to this exact MCP resource URL")
        hashes = [credential.sha256 for credential in self.credentials]
        if len(set(hashes)) != len(hashes):
            raise ValueError("Duplicate service credential hashes")
        public_keys = _string_map(settings.context_public_keys_json)
        if public_keys:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

            for pem in public_keys.values():
                key = serialization.load_pem_public_key(pem.encode())
                if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
                    raise ValueError("AgentContext requires RSA public keys of at least 2048 bits")
        secret_ring = _string_map(settings.context_secret_ring_json)
        self.verifier = AgentContextVerifier(
            settings.context_secret,
            max_ttl_seconds=settings.context_max_ttl_seconds,
            signing_algorithm=settings.context_algorithm,
            signing_key_id=settings.context_key_id,
            signing_key_ring=secret_ring,
            verification_public_key_ring=public_keys,
            jwks_provider=JwksPublicKeyProvider(
                settings.context_jwks_url,
                cache_ttl_seconds=settings.context_jwks_cache_ttl_seconds,
                timeout_seconds=settings.connect_timeout_seconds,
            )
            if settings.context_jwks_url
            else None,
        )
        self.context_configured = (
            bool(public_keys or settings.context_jwks_url)
            if settings.context_algorithm == "RS256"
            else bool(settings.context_secret)
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.credentials
            and self.context_configured
            and self.settings.gateway_base_url
            and self.settings.gateway_service_token
        )

    @property
    def ready(self) -> bool:
        return self.configured and any(
            credential.expires_at > time.time() for credential in self.credentials
        )

    def authenticate(self, authorization: str) -> ServiceCredential:
        if not authorization.startswith("Bearer ") or len(authorization) > 4096:
            raise AuthenticationFailure("invalid_service_credential")
        token = authorization[7:]
        if len(token) < 32 or any(char.isspace() for char in token):
            raise AuthenticationFailure("invalid_service_credential")
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for credential in self.credentials:
            if hmac.compare_digest(digest, credential.sha256):
                match = credential
        if match is None or match.expires_at <= time.time():
            raise AuthenticationFailure("invalid_service_credential")
        return match

    async def invocation(self, request: Any, *, write: bool) -> InvocationContext:
        principal = request.scope.get("state", {}).get("mcp_principal")
        if not isinstance(principal, ServiceCredential):
            raise AuthenticationFailure("invalid_service_credential")
        if ("fitness:write" if write else "fitness:read") not in principal.scopes:
            raise AuthenticationFailure("insufficient_scope")
        headers = request.headers
        signed_context = headers.get("x-agent-context", "")
        request_id = headers.get("x-request-id", "")
        if request_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise AuthenticationFailure("invalid_request_id")
        trace_id = headers.get("x-trace-id")
        if trace_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", trace_id):
            raise AuthenticationFailure("invalid_trace_id")
        if headers.get("x-tool-contract-version") != "1":
            raise AuthenticationFailure("contract_version_mismatch")
        confirmation = headers.get("x-confirmation-token")
        if write and (not confirmation or len(confirmation) > 16384):
            raise AuthenticationFailure("confirmation_required")
        if confirmation and (
            len(confirmation) > 16384 or any(char in confirmation for char in "\r\n")
        ):
            raise AuthenticationFailure("invalid_confirmation")
        now = time.time()
        deadline = now + self.settings.max_deadline_seconds
        raw_deadline = headers.get("x-tool-deadline")
        if raw_deadline is not None:
            try:
                value = float(raw_deadline)
            except ValueError as exc:
                raise AuthenticationFailure("invalid_deadline") from exc
            if not math.isfinite(value) or value <= now:
                raise AuthenticationFailure("invalid_deadline")
            deadline = min(deadline, value)
        try:
            # Verification and any fixed-URL JWKS refresh share the execution budget.
            # A cancelled request need not wait for a bounded synchronous key refresh.
            with anyio.fail_after(max(0, deadline - time.time())):
                identity = await anyio.to_thread.run_sync(
                    self.verifier.verify, signed_context, abandon_on_cancel=True
                )
        except TimeoutError as exc:
            raise AuthenticationFailure("deadline_exceeded") from exc
        except Exception as exc:
            raise AuthenticationFailure("invalid_agent_context") from exc
        return InvocationContext(
            identity, signed_context, principal, request_id, trace_id, confirmation, deadline
        )


def _string_map(value: str) -> dict[str, str]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str) and key and item
        for key, item in parsed.items()
    ):
        raise ValueError("Key rings must be JSON string maps")
    return parsed
