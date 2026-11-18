"""Low-level API adapter; it does not establish an isolated environment.

The runner must not enable real execution until fixture, identity, audit, and
environment isolation have independently been implemented and verified.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Literal
from uuid import uuid4

import httpx

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class AgentHTTP:
    def __init__(self, client: httpx.AsyncClient, signed_context: str) -> None:
        if not signed_context:
            raise ValueError("a test identity is required")
        self._client = client
        self._signed_context = signed_context
        self.conversation_id = str(uuid4())
        self._decisions: dict[str, tuple[str, str]] = {}

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Context": self._signed_context}

    async def chat(self, message: str) -> dict[str, Any]:
        headers = self._headers() | {"X-Request-Id": str(uuid4())}
        response = await self._client.post(
            "/api/v1/agent/chat",
            json={"conversation_id": self.conversation_id, "message": message, "locale": "zh-CN"},
            headers=headers,
            # Even when the caller configured automatic redirects, do not send
            # the signed test identity or input to a redirect destination.
            follow_redirects=False,
        )
        response.raise_for_status()
        return self._object(response)

    @staticmethod
    def _path(confirmation_id: str) -> str:
        if confirmation_id in {".", ".."} or not _ID.fullmatch(confirmation_id):
            raise ValueError("invalid confirmation id")
        return f"/api/v1/agent/confirmations/{confirmation_id}"

    @staticmethod
    def _object(response: httpx.Response) -> dict[str, Any]:
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("invalid API response")
        return value

    async def confirmation(self, confirmation_id: str) -> dict[str, Any]:
        response = await self._client.get(
            self._path(confirmation_id), headers=self._headers(), follow_redirects=False
        )
        response.raise_for_status()
        return self._object(response)

    async def decide(
        self,
        confirmation_id: str,
        decision: Literal["APPROVE", "REJECT"],
    ) -> dict[str, Any]:
        if decision not in ("APPROVE", "REJECT"):
            raise ValueError("invalid decision")
        path = self._path(confirmation_id)
        previous = self._decisions.get(confirmation_id)
        if previous is not None and previous[0] != decision:
            raise ValueError("confirmation already has a different decision")
        if previous is None:
            previous = (decision, str(uuid4()))
            self._decisions[confirmation_id] = previous
        response = await self._client.post(
            f"{path}/decisions",
            headers=self._headers(),
            json={"decision": decision, "decision_request_id": previous[1]},
            follow_redirects=False,
        )
        response.raise_for_status()
        return self._object(response)

    async def await_terminal(
        self,
        confirmation_id: str,
        *,
        expected_authorization: str,
        expected_execution: set[str],
        timeout_seconds: float,
        poll_seconds: float = 0.2,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0 or poll_seconds <= 0 or not expected_execution:
            raise ValueError("a positive timeout, poll interval and terminal states are required")
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("confirmation did not reach the expected terminal state")
            try:
                response = await asyncio.wait_for(
                    self.confirmation(confirmation_id), timeout=remaining
                )
            except TimeoutError as exc:
                raise TimeoutError(
                    "confirmation did not reach the expected terminal state"
                ) from exc
            if (
                response.get("authorization_status") == expected_authorization
                and response.get("execution_status") in expected_execution
            ):
                return response
            await asyncio.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
