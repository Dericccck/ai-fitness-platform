from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin_notifications import router
from app.infrastructure.agent_context import AgentIdentity
from app.notifications.outbox import NotificationDeliveryAttemptRecord


class FakeVerifier:
    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("admin-1", frozenset({"org-1"}), self.roles, 1, 2)


class FakeConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext()


class FakeDatabase:
    engine = FakeEngine()


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}

    async def list_delivery_attempts(
        self, connection: object, **kwargs: Any
    ) -> list[NotificationDeliveryAttemptRecord]:
        self.filters = kwargs
        return [
            NotificationDeliveryAttemptRecord(
                id=7,
                outbox_id="outbox-7",
                notification_type="MEMORY_CANDIDATE_PENDING",
                organization_id="org-1",
                channel="IN_APP",
                attempt_no=2,
                status="FINAL_FAILED",
                error_code="NOTIFICATION_DELIVERY_FAILED",
                provider_message_id=None,
                started_at=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
                finished_at=datetime(2026, 8, 15, 10, 0, 1, tzinfo=UTC),
            )
        ]


def build_app(roles: frozenset[str]) -> tuple[FastAPI, FakeOutboxRepository]:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier(roles)
    app.state.database = FakeDatabase()
    outbox = FakeOutboxRepository()
    app.state.notification_outbox = outbox
    app.include_router(router)
    return app, outbox


async def test_platform_admin_can_query_sanitized_delivery_attempts() -> None:
    app, outbox = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/notifications/delivery-attempts",
            headers={"X-Agent-Context": "signed-context"},
            params={
                "organization_id": "org-1",
                "delivery_status": "FINAL_FAILED",
                "limit": "20",
            },
        )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "FINAL_FAILED"
    assert "subject_user_id" not in response.json()[0]
    assert "aggregate_id" not in response.json()[0]
    assert outbox.filters["organization_id"] == "org-1"
    assert outbox.filters["status"] == "FINAL_FAILED"


async def test_non_admin_cannot_query_delivery_attempts() -> None:
    app, _ = build_app(frozenset({"STUDENT"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/notifications/delivery-attempts",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 403
