from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.capabilities import router
from app.infrastructure.agent_context import AgentIdentity


class FakeVerifier:
    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("user-1", frozenset({"org-1"}), self.roles, 1, 2)


class FakeRegistry:
    def public_specs(self) -> list[dict[str, object]]:
        return [
            {
                "name": "fitness.training.plan.create_draft.v1",
                "description": "创建结构化训练计划草案；草案不能直接发布，必须经过教练审核。",
                "allowed_roles": ["COACH", "ORGANIZATION_ADMIN"],
                "read_only": False,
                "requires_confirmation": True,
                "confirmation_action": "CREATE_TRAINING_DRAFT",
            },
            {
                "name": "fitness.training.day.record_execution.v1",
                "description": "记录本人训练日执行结果。",
                "allowed_roles": ["STUDENT"],
                "read_only": False,
                "requires_confirmation": True,
                "confirmation_action": "RECORD_TRAINING_DAY_EXECUTION",
            },
            {
                "name": "fitness.operations.metric.query.v1",
                "description": "查询固定经营指标。",
                "allowed_roles": ["SYSTEM_ADMIN", "ORGANIZATION_ADMIN"],
                "read_only": True,
                "requires_confirmation": False,
                "confirmation_action": None,
            },
        ]


def build_app(roles: frozenset[str]) -> FastAPI:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier(roles)
    app.state.tool_registry = FakeRegistry()
    app.include_router(router)
    return app


async def test_student_receives_only_student_capabilities() -> None:
    transport = ASGITransport(app=build_app(frozenset({"STUDENT"})))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agent/capabilities",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["roles"] == ["STUDENT"]
    assert [item["id"] for item in payload["items"]] == ["fitness.training.day.record_execution.v1"]
    assert payload["items"][0]["requires_confirmation"] is True
    assert payload["items"][0]["domain"] == "TRAINING"


async def test_organization_admin_receives_training_and_operations_capabilities() -> None:
    transport = ASGITransport(app=build_app(frozenset({"ORGANIZATION_ADMIN"})))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agent/capabilities",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == [
        "fitness.operations.metric.query.v1",
        "fitness.training.plan.create_draft.v1",
    ]
    assert payload["items"][0]["read_only"] is True
    assert payload["items"][1]["confirmation_action"] == "CREATE_TRAINING_DRAFT"


async def test_capability_catalog_supports_private_etag_cache() -> None:
    transport = ASGITransport(app=build_app(frozenset({"COACH"})))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/api/v1/agent/capabilities",
            headers={"X-Agent-Context": "signed-context"},
        )
        cached = await client.get(
            "/api/v1/agent/capabilities",
            headers={
                "X-Agent-Context": "signed-context",
                "If-None-Match": first.headers["etag"],
            },
        )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, max-age=300, must-revalidate"
    assert cached.status_code == 304
    assert cached.content == b""


async def test_capability_catalog_requires_signed_context() -> None:
    transport = ASGITransport(app=build_app(frozenset({"STUDENT"})))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/agent/capabilities")

    assert response.status_code == 401
