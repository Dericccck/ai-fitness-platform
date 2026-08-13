from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.confirmations import router
from app.confirmation.models import ConfirmationRecord
from app.infrastructure.agent_context import AgentIdentity


def identity(subject: str = "coach-1") -> AgentIdentity:
    return AgentIdentity(
        subject=subject,
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"COACH"}),
        issued_at=1,
        expires_at=2,
    )


class FakeVerifier:
    def verify(self, token: str) -> AgentIdentity:
        if token != "signed-context":
            raise ValueError("invalid token")
        return identity()


def record() -> ConfirmationRecord:
    now = datetime.now(UTC)
    return ConfirmationRecord(
        id="confirmation-1",
        protocol_version=1,
        thread_id="fitness:thread",
        subject_user_id="coach-1",
        organization_id="org-1",
        tool_id="fitness.training.plan.create_draft.v1",
        risk_level="WRITE",
        action="CREATE_TRAINING_DRAFT",
        resource_type="training_plan",
        resource_id=None,
        expected_resource_version=None,
        request_id="request-1",
        payload_hash="a" * 64,
        display_summary={"operation": "创建训练计划草案", "details": {"title": "基础力量"}},
        payload_ciphertext=b"secret-ciphertext",
        payload_key_version="test-v1",
        authorization_status="PENDING",
        execution_status="NOT_STARTED",
        version=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        actor_roles=("COACH",),
        actor_organization_ids=("org-1",),
    )


class FakeConfirmationService:
    def __init__(self) -> None:
        self.current = record()
        self.decisions: list[dict[str, Any]] = []

    async def get_for_subject(
        self, confirmation_id: str, requested_identity: AgentIdentity
    ) -> ConfirmationRecord:
        assert confirmation_id == self.current.id
        assert requested_identity.subject == self.current.subject_user_id
        return self.current

    async def decide(self, confirmation_id: str, **kwargs: Any) -> ConfirmationRecord:
        assert confirmation_id == self.current.id
        self.decisions.append(kwargs)
        self.current = self.current.approve(datetime.now(UTC), kwargs["decision_request_id"])
        return self.current


class FakeSupervisor:
    async def resume_confirmation(self, confirmation_id: str, **kwargs: Any) -> None:
        assert confirmation_id == "confirmation-1"
        assert kwargs["identity"].subject == "coach-1"
        assert kwargs["gateway_context"].confirmation_token is None
        assert kwargs["thread_id"] == "fitness:thread"


def build_app(service: FakeConfirmationService) -> FastAPI:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier()
    app.state.confirmation_service = service
    app.state.supervisor = FakeSupervisor()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    return app


async def test_get_confirmation_returns_only_redacted_fields() -> None:
    app = build_app(FakeConfirmationService())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agent/confirmations/confirmation-1",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["authorization_status"] == "PENDING"
    assert body["display_summary"]["details"]["title"] == "基础力量"
    assert "payload_ciphertext" not in body
    assert "payload_hash" not in body
    assert "credential_jti" not in body


async def test_confirmation_decision_forwards_signed_identity_and_idempotency_key() -> None:
    service = FakeConfirmationService()
    app = build_app(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/confirmations/confirmation-1/decisions",
            headers={"X-Agent-Context": "signed-context", "X-Trace-ID": "trace-1"},
            json={"decision": "APPROVE", "decision_request_id": "decision-1"},
        )

    assert response.status_code == 200
    assert response.json()["authorization_status"] == "APPROVED"
    assert service.decisions[0]["decision"] == "APPROVE"
    assert service.decisions[0]["decision_request_id"] == "decision-1"
    assert service.decisions[0]["identity"].subject == "coach-1"
    assert service.decisions[0]["trace_id"] == "trace-1"


async def test_confirmation_api_rejects_extra_decision_fields() -> None:
    app = build_app(FakeConfirmationService())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/confirmations/confirmation-1/decisions",
            headers={"X-Agent-Context": "signed-context"},
            json={
                "decision": "APPROVE",
                "decision_request_id": "decision-1",
                "organization_id": "attacker-org",
            },
        )

    assert response.status_code == 422
