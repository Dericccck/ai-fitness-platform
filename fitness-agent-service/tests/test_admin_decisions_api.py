from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin_decisions import router
from app.infrastructure.agent_context import AgentIdentity


class Verifier:
    def __init__(self, roles: frozenset[str], organizations: frozenset[str]) -> None:
        self.roles = roles
        self.organizations = organizations

    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("admin-1", self.organizations, self.roles, 1, 2)


class ConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class Engine:
    def connect(self) -> ConnectionContext:
        return ConnectionContext()


class Database:
    engine = Engine()


class Repository:
    def __init__(self) -> None:
        self.organization_ids: tuple[str, ...] = ()

    async def list_outcomes(self, connection: object, **kwargs: Any) -> list[dict[str, Any]]:
        self.organization_ids = kwargs["organization_ids"]
        start = datetime(2026, 9, 1, tzinfo=UTC)
        common = {
            "metric": "RETENTION_RATE",
            "window_start": start,
            "window_end": start + timedelta(days=7),
        }
        return [
            {**common, "group_name": "TREATMENT", "value": 0.62, "sample_size": 100},
            {**common, "group_name": "CONTROL", "value": 0.5, "sample_size": 120},
            {**common, "group_name": "ALL", "value": 0.56, "sample_size": 220},
        ]


def build_app(
    roles: frozenset[str], organizations: frozenset[str] = frozenset({"org-1"})
) -> tuple[FastAPI, Repository]:
    app = FastAPI()
    app.state.context_verifier = Verifier(roles, organizations)
    app.state.database = Database()
    repository = Repository()
    app.state.decision_repository = repository
    app.include_router(router)
    return app, repository


async def test_admin_can_read_incremental_case_effect_without_raw_subjects() -> None:
    app, _ = build_app(frozenset({"ORGANIZATION_ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/decisions/cases/case-1/effects",
            headers={"X-Agent-Context": "signed"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "metric": "RETENTION_RATE",
            "window_start": "2026-09-01T00:00:00Z",
            "window_end": "2026-09-08T00:00:00Z",
            "treatment_value": 0.62,
            "treatment_samples": 100,
            "control_value": 0.5,
            "control_samples": 120,
            "absolute_effect": 0.12,
            "relative_effect_percent": 24.0,
        }
    ]
    assert "subject" not in response.text


async def test_non_admin_cannot_access_decision_governance() -> None:
    app, _ = build_app(frozenset({"COACH"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/decisions/cases/case-1/effects",
            headers={"X-Agent-Context": "signed"},
        )

    assert response.status_code == 403


async def test_platform_admin_can_supply_explicit_organization_scope() -> None:
    app, repository = build_app(frozenset({"SYSTEM_ADMIN"}), frozenset())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/decisions/cases/case-1/effects",
            headers={"X-Agent-Context": "signed"},
            params={"organization_id": "org-platform-scope"},
        )

    assert response.status_code == 200
    assert repository.organization_ids == ("org-platform-scope",)
