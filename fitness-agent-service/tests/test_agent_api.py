from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agent.supervisor import SupervisorResponse
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.agent import router
from app.infrastructure.agent_context import AgentIdentity


class FakeSupervisor:
    def __init__(self, response: SupervisorResponse) -> None:
        self.response = response
        self.requests: list[Any] = []

    async def invoke(self, request: Any) -> SupervisorResponse:
        self.requests.append(request)
        return self.response


class FakeContextVerifier:
    def verify(self, token: str) -> AgentIdentity:
        assert token == "signed-context"
        return AgentIdentity(
            subject="user-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"STUDENT"}),
            issued_at=1,
            expires_at=2,
        )


def build_app(supervisor: FakeSupervisor) -> FastAPI:
    test_app = FastAPI()
    test_app.state.supervisor = supervisor
    test_app.state.context_verifier = FakeContextVerifier()
    test_app.add_middleware(RequestContextMiddleware)
    test_app.include_router(router)
    return test_app


async def test_chat_requires_signed_agent_context() -> None:
    app = build_app(FakeSupervisor(SupervisorResponse("unused", "FITNESS_COACHING", 0, None, None)))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"conversation_id": "conversation-1", "message": "制定减脂计划"},
        )

    assert response.status_code == 401


async def test_chat_forwards_only_signed_context_to_supervisor() -> None:
    supervisor = FakeSupervisor(SupervisorResponse("已查询真实课程。", "BOOKING", 1, 12, 8))
    app = build_app(supervisor)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/chat",
            headers={
                "X-Agent-Context": "signed-context",
                "X-Request-ID": "request-123",
                "X-Trace-ID": "trace-456",
            },
            json={
                "conversation_id": "conversation-1",
                "message": "查询我的课程",
                "locale": "zh-CN",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": "conversation-1",
        "answer": "已查询真实课程。",
        "route": "BOOKING",
        "tool_steps": 1,
        "input_tokens": 12,
        "output_tokens": 8,
    }
    assert supervisor.requests[0].gateway_context.signed_context == "signed-context"
    assert supervisor.requests[0].gateway_context.request_id == "request-123"
    assert supervisor.requests[0].gateway_context.trace_id == "trace-456"


async def test_chat_rejects_extra_request_fields() -> None:
    app = build_app(FakeSupervisor(SupervisorResponse("unused", "FITNESS_COACHING", 0, None, None)))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"X-Agent-Context": "signed-context"},
            json={
                "conversation_id": "conversation-1",
                "message": "制定计划",
                "organization_id": "must-not-be-accepted",
            },
        )

    assert response.status_code == 422
