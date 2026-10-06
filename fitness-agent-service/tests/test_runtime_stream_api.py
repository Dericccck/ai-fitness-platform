from __future__ import annotations

from typing import Any
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.agent.supervisor import SupervisorResponse
from app.core.config import Settings
from tests.test_agent_api import FakeSupervisor, build_app
from tests.test_runtime_streaming import MemoryRunRepository, manager


class StreamSupervisor(FakeSupervisor):
    async def stream(self, request: Any, emit: Any) -> SupervisorResponse:
        self.requests.append(request)
        await emit("answer.delta", {"text": "安全回答"})
        return self.response


def stream_app() -> tuple[Any, StreamSupervisor]:
    supervisor = StreamSupervisor(SupervisorResponse("安全回答", "FITNESS_COACHING", 0, 10, 5))
    app = build_app(supervisor)
    app.state.settings = Settings(_env_file=None, chat_rate_limit_enabled=False)
    app.state.chat_runs = manager(MemoryRunRepository())
    return app, supervisor


async def test_stream_requires_identity_and_rejects_client_scope_injection() -> None:
    app, supervisor = stream_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"run_id": str(uuid4()), "conversation_id": "conversation", "message": "热身"}
        assert (await client.post("/api/v1/agent/chat/stream", json=payload)).status_code == 401
        assert (
            await client.post(
                "/api/v1/agent/chat/stream",
                headers={"X-Agent-Context": "signed-context"},
                json={**payload, "organization_id": "other"},
            )
        ).status_code == 422
    assert not supervisor.requests
    await app.state.chat_runs.close()


async def test_stream_repeated_run_is_canonical_and_not_executed_again() -> None:
    app, supervisor = stream_app()
    headers = {"X-Agent-Context": "signed-context"}
    payload = {"run_id": str(uuid4()), "conversation_id": "conversation", "message": "热身"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/agent/chat/stream", headers=headers, json=payload)
        assert first.status_code == 200 and "text/event-stream" in first.headers["content-type"]
        assert "run.completed" in first.text and "安全回答" in first.text
        repeated = await client.post("/api/v1/agent/chat/stream", headers=headers, json=payload)
        assert repeated.status_code == 200 and "run.completed" in repeated.text
        recovered = await client.get("/api/v1/agent/runs/" + payload["run_id"], headers=headers)
        assert recovered.json()["response"]["answer"] == "安全回答"
        changed = await client.post(
            "/api/v1/agent/chat/stream", headers=headers, json={**payload, "message": "修改"}
        )
        assert changed.status_code == 409
    assert len(supervisor.requests) == 1
    await app.state.chat_runs.close()


async def test_chat_quota_fail_closed_returns_retry_after() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    app, supervisor = stream_app()
    app.state.settings.chat_rate_limit_enabled = True
    app.state.cache = SimpleNamespace(consume_fixed_window=AsyncMock(return_value=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/chat/stream",
            headers={"X-Agent-Context": "signed-context"},
            json={"run_id": str(uuid4()), "conversation_id": "c", "message": "热身"},
        )
        assert response.status_code == 429 and response.headers["Retry-After"] == "60"
        app.state.cache.consume_fixed_window.side_effect = ConnectionError(
            "secret-connection-string"
        )
        failed = await client.post(
            "/api/v1/agent/chat",
            headers={"X-Agent-Context": "signed-context"},
            json={"conversation_id": "c", "message": "热身"},
        )
        assert failed.status_code == 503 and "secret-connection-string" not in failed.text
    assert not supervisor.requests
    await app.state.chat_runs.close()
