"""One bounded HTTP attempt per tool invocation; Java remains the authorization authority."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Literal

import anyio
import httpx
from fitness_tool_contracts.catalog import ToolSpec, error_envelope, success_envelope
from fitness_tool_contracts.commands import PreparedGatewayCommand
from pydantic import ValidationError

from .config import Settings
from .security import InvocationContext


class ToolFailure(Exception):
    def __init__(self, code: str, state: Literal["REJECTED", "NOT_SENT", "UNKNOWN"]):
        super().__init__(code)
        self.code = code
        self.state = state

    @property
    def envelope(self) -> dict[str, Any]:
        return error_envelope(self.code, self.state)


class GatewayAdapter:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.gateway_base_url or "http://unconfigured.invalid",
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=settings.max_concurrent_gateway_calls),
        )
        self.capacity = anyio.Semaphore(settings.max_concurrent_gateway_calls)

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def execute(
        self, spec: ToolSpec, command: PreparedGatewayCommand, context: InvocationContext
    ) -> dict[str, Any]:
        remaining = context.deadline - time.time()
        if remaining <= 0:
            raise ToolFailure("DEADLINE_EXCEEDED", "NOT_SENT")
        headers = {
            "X-Internal-Service-Token": self.settings.gateway_service_token,
            "X-Agent-Context": context.signed_context,
            "X-Request-ID": context.request_id,
        }
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        if context.confirmation_token:
            headers["X-Confirmation-Token"] = context.confirmation_token
        if command.body is not None:
            headers["Content-Type"] = "application/json"
        started = False
        try:
            with anyio.fail_after(remaining):
                async with self.capacity:
                    remaining = context.deadline - time.time()
                    if remaining <= 0:
                        raise ToolFailure("DEADLINE_EXCEEDED", "NOT_SENT")
                    timeout = httpx.Timeout(
                        remaining, connect=min(self.settings.connect_timeout_seconds, remaining)
                    )
                    started = True
                    async with self.client.stream(
                        command.method,
                        command.path,
                        params=command.query,
                        content=command.body,
                        headers=headers,
                        timeout=timeout,
                    ) as response:
                        if response.status_code != 200:
                            self._reject_status(response.status_code, spec.write)
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.settings.max_result_bytes:
                                raise ToolFailure(
                                    "RESULT_TOO_LARGE", "UNKNOWN" if spec.write else "REJECTED"
                                )
                            chunks.append(chunk)
                        try:
                            value = json.loads(
                                b"".join(chunks),
                                parse_constant=_reject_constant,
                                parse_float=_finite_float,
                            )
                            return success_envelope(spec, value)
                        except (ValueError, TypeError, ValidationError):
                            raise ToolFailure(
                                "PROTOCOL_ERROR", "UNKNOWN" if spec.write else "REJECTED"
                            ) from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ToolFailure("UNAVAILABLE", "NOT_SENT") from None
        except (httpx.HTTPError, TimeoutError):
            state = (
                "UNKNOWN" if spec.write and started else "NOT_SENT" if not started else "REJECTED"
            )
            raise ToolFailure("UNAVAILABLE", state) from None

    @staticmethod
    def _reject_status(status: int, write: bool) -> None:
        codes = {
            400: "BAD_REQUEST",
            401: "AUTHENTICATION_FAILED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "BAD_REQUEST",
        }
        if status in codes:
            raise ToolFailure(codes[status], "REJECTED")
        # Redirects are not followed, including same-origin redirects: a fixed route
        # must not forward identity/confirmation credentials to another resource.
        raise ToolFailure("UNAVAILABLE", "UNKNOWN" if write else "REJECTED")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Non-finite JSON number")
    return parsed
