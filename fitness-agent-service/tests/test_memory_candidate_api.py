from datetime import UTC, datetime, timedelta
from typing import Any, Self

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.memory_candidates import router
from app.infrastructure.agent_context import AgentIdentity
from app.memory.candidate import MemoryCandidate, MemoryCandidateRecord
from app.notifications.outbox import InAppNotificationRecord


class FakeVerifier:
    def verify(self, token: str) -> AgentIdentity:
        assert token == "signed-context"
        return AgentIdentity(
            subject="student-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"STUDENT"}),
            issued_at=1,
            expires_at=2,
        )


class FakeCandidateService:
    async def list_pending(self, **_: Any) -> list[MemoryCandidateRecord]:
        now = datetime.now(UTC)
        return [
            MemoryCandidateRecord(
                id="candidate-1",
                subject_user_id="student-1",
                organization_id="org-1",
                candidate=MemoryCandidate(
                    memory_type="EQUIPMENT_AVAILABILITY",
                    memory_key="available_equipment",
                    value="弹力带",
                ),
                payload_hash="a" * 64,
                source_thread_id="fitness:thread-1",
                source_request_id="request-1",
                status="PENDING",
                expires_at=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            )
        ]


class FakeNotificationRepository:
    async def list_in_app(self, *_: Any, **__: Any) -> list[InAppNotificationRecord]:
        now = datetime.now(UTC)
        return [
            InAppNotificationRecord(
                id="notification-1",
                notification_type="MEMORY_CANDIDATE_PENDING",
                subject_user_id="student-1",
                organization_id="org-1",
                aggregate_type="memory_candidate",
                aggregate_id="candidate-1",
                status="UNREAD",
                created_at=now,
                read_at=None,
            )
        ]


class FakeConnection:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeDatabase:
    class Engine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

    engine = Engine()


def build_app() -> FastAPI:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier()
    app.state.memory_candidate_service = FakeCandidateService()
    app.state.notification_outbox = FakeNotificationRepository()
    app.state.database = FakeDatabase()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    return app


async def test_memory_candidate_inbox_joins_pending_candidate_and_notification() -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agent/memory-candidates/inbox",
            headers={"X-Agent-Context": "signed-context"},
            params={"organization_id": "org-1"},
        )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "candidate": {
                "id": "candidate-1",
                "organization_id": "org-1",
                "memory_type": "EQUIPMENT_AVAILABILITY",
                "memory_key": "available_equipment",
                "value": "弹力带",
                "unit": None,
                "status": "PENDING",
                "expires_at": response.json()["items"][0]["candidate"]["expires_at"],
                "created_at": response.json()["items"][0]["candidate"]["created_at"],
                "updated_at": response.json()["items"][0]["candidate"]["updated_at"],
                "decision_request_id": None,
                "decided_at": None,
                "memory_id": None,
            },
            "notification_id": "notification-1",
            "notification_status": "UNREAD",
            "notification_created_at": response.json()["items"][0]["notification_created_at"],
        }
    ]


async def test_memory_candidate_inbox_requires_signed_context() -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agent/memory-candidates/inbox",
            params={"organization_id": "org-1"},
        )

    assert response.status_code == 401
