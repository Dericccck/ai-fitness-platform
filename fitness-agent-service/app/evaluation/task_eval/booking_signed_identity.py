"""Exercise actual signed identities and confirmation issuance using local doubles.

The model, business gateway, persistence, and transport remain local substitutes.
This is a protocol test, not a real-model or cross-service task benchmark.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
from contextlib import redirect_stdout
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPStatusError
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.request_body import gateway_confirmation_body
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.gateway_client import GatewayRequestContext

from . import booking_local as local
from .agent_http import AgentHTTP


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _identity(secret: str, *, subject: str, org: str, expired: bool = False) -> str:
    now = int(time.time())
    claims = {
        "sub": subject,
        "orgs": [org],
        "roles": ["STUDENT"],
        "iat": now - 120 if expired else now,
        "exp": now - 60 if expired else now + 120,
        "nonce": uuid4().hex,
        "alg": "HS256",
        "kid": "eval",
    }
    payload = json.dumps(claims, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


class SignedGateway(local.Gateway):
    """Independently validate the issued HMAC and its business body binding."""

    def __init__(self, repository: local.Repository, secret: str) -> None:
        super().__init__(repository, "approve")
        self.secret = secret.encode()
        self.signature_verified = False

    def _verify_confirmation(self, token: str, payload: dict[str, Any]) -> None:
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError("invalid test confirmation credential")
        raw = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
        signature = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        expected_signature = hmac.new(self.secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("invalid test confirmation signature")
        claims = json.loads(raw)
        record = self.repository.current
        expected_body = gateway_confirmation_body(
            record.tool_id,
            payload,
            contract_version=record.contract_version,
            resource_id=record.resource_id,
        )
        expected = {
            "alg": "HS256",
            "kid": "eval",
            "sub": record.subject_user_id,
            "action": record.action,
            "resource": record.resource_id or "org-1:student-1",
            "confirmation_id": record.id,
            "tool_id": record.tool_id,
            "organization_id": record.organization_id,
            "payload_hash": record.payload_hash,
            "request_id": record.request_id,
            "jti": record.credential_jti,
            "request_body_sha256": hashlib.sha256(expected_body).hexdigest(),
            "body_binding_version": 1,
        }
        if any(claims.get(key) != value for key, value in expected.items()):
            raise ValueError("invalid test confirmation binding")
        if (
            not isinstance(claims.get("exp"), int)
            or isinstance(claims["exp"], bool)
            or claims["exp"] <= int(time.time())
            or claims["exp"] > int(record.expires_at.timestamp())
        ):
            raise ValueError("expired test confirmation credential")
        self.signature_verified = True

    async def create_booking(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._verify_confirmation(context.confirmation_token or "", payload)
        # The parent business substitute accepts only its sentinel token; the
        # actual credential was independently checked above before any write.
        return await super().create_booking(
            replace(context, confirmation_token="local-test-token"), payload
        )


async def evaluate_signed_identity() -> dict[str, Any]:
    context_secret = secrets.token_urlsafe(32)
    confirmation_secret = secrets.token_urlsafe(32)
    repository = local.Repository()
    gateway = SignedGateway(repository, confirmation_secret)
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=secrets.token_bytes(32), key_version="eval"),
        ConfirmationTokenIssuer(secret=confirmation_secret, signing_key_id="eval"),
    )
    app = FastAPI()
    app.state.context_verifier = AgentContextVerifier(context_secret, signing_key_id="eval")
    app.state.confirmation_service = service
    app.state.supervisor = Supervisor(
        cast(Any, local.Model()),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    checks: dict[str, bool] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            owner = _identity(context_secret, subject="student-1", org="org-1")
            agent = AgentHTTP(client, owner)
            chat = await agent.chat("请帮我预约课程")
            cid = chat.get("confirmation_id")
            checks["confirmation_required"] = (
                chat.get("status") == "CONFIRMATION_REQUIRED" and isinstance(cid, str) and bool(cid)
            )
            if not checks["confirmation_required"] or not isinstance(cid, str):
                return {"status": "FAIL", "checks": checks, "origin": "local_signed_protocol"}
            path = f"/api/v1/agent/confirmations/{cid}"
            altered = owner.split(".")[0] + "." + _encode(b"wrong-signature")
            unauthorized = (
                ("tampered", altered, 401),
                (
                    "expired",
                    _identity(context_secret, subject="student-1", org="org-1", expired=True),
                    401,
                ),
                ("other_subject", _identity(context_secret, subject="other", org="org-1"), 404),
                ("other_org", _identity(context_secret, subject="student-1", org="org-2"), 404),
            )
            for label, token, expected_status in unauthorized:
                headers = {"X-Agent-Context": token}
                read = await client.get(path, headers=headers)
                decision = await client.post(
                    f"{path}/decisions",
                    headers=headers,
                    json={"decision": "APPROVE", "decision_request_id": uuid4().hex},
                )
                checks[f"{label}_read_denied"] = read.status_code == expected_status
                checks[f"{label}_decision_denied"] = decision.status_code == expected_status
            checks["no_write_before_owner_decision"] = (
                gateway.observe()["after"]["booking_count"] == 0
            )
            try:
                approval = await agent.decide(cid, "APPROVE")
            except HTTPStatusError:
                checks["owner_approval_succeeded"] = False
                failed_evidence = gateway.observe()
                checks["no_write_on_gateway_error"] = (
                    failed_evidence["after"]["booking_count"] == 0
                    and failed_evidence["credit_delta"] == 0
                    and not failed_evidence["commits"]
                )
                return {"status": "FAIL", "checks": checks, "origin": "local_signed_protocol"}
            repeat = await agent.decide(cid, "APPROVE")
            evidence = gateway.observe()
            checks["owner_approval_succeeded"] = approval.get("execution_status") == "SUCCEEDED"
            checks["same_decision_idempotent"] = repeat.get("execution_status") == "SUCCEEDED"
            checks["confirmation_signature_and_binding_verified"] = gateway.signature_verified
            checks["one_audited_write_and_credit"] = (
                evidence["after"]["booking_count"] == 1
                and evidence["credit_delta"] == -1
                and len(evidence["commits"]) == 1
                and evidence["commits"][0]["authorized_before_commit"]
            )
            return {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "checks": checks,
                "origin": "local_signed_protocol",
            }
    finally:
        gateway.db.close()


def main() -> int:
    try:
        with redirect_stdout(sys.stderr):
            result = asyncio.run(evaluate_signed_identity())
    except Exception:  # noqa: BLE001 - do not expose credentials or internal exception details
        result = {"status": "ERROR", "reason": "LOCAL_SIGNED_PROTOCOL_ERROR"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "FAIL": 2, "ERROR": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
