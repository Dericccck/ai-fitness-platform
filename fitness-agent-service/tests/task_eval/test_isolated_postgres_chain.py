"""Real Supervisor + HTTP routes + PostgreSQL confirmation state on disposable DB.

The model/router and SQLite business gateway are deterministic test doubles; this
exercise must never be reported as a real-model or cross-service acceptance run.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.request_body import gateway_confirmation_body
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.core.config import Settings
from app.evaluation.task_eval import booking_local
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_signed_identity import _identity
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayRequestContext

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RUNTIME_DELIVERY_TESTS") != "1",
    reason="requires isolated runtime launcher",
)


class AuditedGateway:
    """Synthetic business state; verifies the issued credential against persisted PG state."""

    def __init__(self, repository: ConfirmationRepository, secret: str) -> None:
        self.repository = repository
        self.secret = secret.encode()
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE bookings (confirmation_id TEXT UNIQUE);"
            "CREATE TABLE credits (remaining INTEGER NOT NULL); INSERT INTO credits VALUES (10);"
        )
        self.calls = 0

    async def create_booking(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        token = context.confirmation_token or ""
        parts = token.split(".")
        assert len(parts) == 2
        raw = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
        signature = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        assert hmac.compare_digest(signature, hmac.new(self.secret, raw, hashlib.sha256).digest())
        claims = json.loads(raw)
        record = await self.repository.get_for_subject(
            claims["confirmation_id"], "student-1", ["org-1"]
        )
        assert (record.authorization_status, record.execution_status) == ("APPROVED", "RUNNING")
        assert claims["exp"] > time.time()
        assert claims["sub"] == record.subject_user_id
        assert claims["organization_id"] == record.organization_id
        assert claims["tool_id"] == record.tool_id
        assert claims["action"] == record.action
        assert claims["request_id"] == record.request_id
        assert claims["payload_hash"] == record.payload_hash
        assert claims["alg"] == "HS256" and claims["kid"] == "eval"
        assert claims["jti"] == record.credential_jti
        assert (
            claims["request_body_sha256"]
            == hashlib.sha256(
                gateway_confirmation_body(
                    record.tool_id,
                    payload,
                    contract_version=record.contract_version,
                    resource_id=record.resource_id,
                )
            ).hexdigest()
        )
        self.calls += 1
        with self.db:
            self.db.execute("INSERT INTO bookings VALUES (?)", (record.id,))
            self.db.execute("UPDATE credits SET remaining = remaining - 1")
        return {"id": "test-booking"}

    def snapshot(self) -> tuple[int, int]:
        return (
            self.db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
            self.db.execute("SELECT remaining FROM credits").fetchone()[0],
        )


@pytest.mark.parametrize("decision", ["REJECT", "APPROVE"])
async def test_agent_confirmation_route_with_persisted_postgres(decision: str) -> None:
    settings = Settings(_env_file=None)
    url = make_url(settings.database_url)
    # Guard even if the enabling environment variable was set accidentally.
    assert url.host == "127.0.0.1" and (url.database or "").startswith(
        "fitness_runtime_acceptance_"
    )
    db = Database(settings)
    repo = ConfirmationRepository(db)
    context_secret, token_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    gateway = AuditedGateway(repo, token_secret)
    registry = build_fitness_tool_registry(cast(Any, gateway))
    confirmation = ConfirmationService(
        repo,
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=secrets.token_bytes(32), key_version="eval"),
        ConfirmationTokenIssuer(secret=token_secret, signing_key_id="eval"),
    )
    app = FastAPI()
    app.state.context_verifier = AgentContextVerifier(context_secret, signing_key_id="eval")
    app.state.confirmation_service = confirmation
    app.state.supervisor = Supervisor(
        cast(Any, booking_local.Model()),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=confirmation,
        intent_router=cast(Any, booking_local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            owner = AgentHTTP(client, _identity(context_secret, subject="student-1", org="org-1"))
            result = await owner.chat("请帮我预约课程")
            assert result["status"] == "CONFIRMATION_REQUIRED"
            cid = result["confirmation_id"]
            assert gateway.snapshot() == (0, 10)
            before = await repo.list_events(cid)
            assert [event.event_type for event in before] == ["CREATED"]
            stranger = AgentHTTP(client, _identity(context_secret, subject="other", org="org-1"))
            denied = await client.post(
                f"/api/v1/agent/confirmations/{cid}/decisions",
                headers=stranger._headers(),
                json={"decision": "APPROVE", "decision_request_id": str(uuid4())},
            )
            assert denied.status_code == 404 and gateway.snapshot() == (0, 10)
            other_org = AgentHTTP(
                client, _identity(context_secret, subject="student-1", org="other-org")
            )
            denied_org = await client.post(
                f"/api/v1/agent/confirmations/{cid}/decisions",
                headers=other_org._headers(),
                json={"decision": "APPROVE", "decision_request_id": str(uuid4())},
            )
            assert denied_org.status_code == 404 and gateway.snapshot() == (0, 10)
            first = await owner.decide(cid, decision)
            repeated = await owner.decide(cid, decision)
            terminal = (
                ("REJECTED", "NOT_STARTED") if decision == "REJECT" else ("APPROVED", "SUCCEEDED")
            )
            assert (first["authorization_status"], first["execution_status"]) == terminal
            assert (repeated["authorization_status"], repeated["execution_status"]) == terminal
            assert gateway.snapshot() == ((0, 10) if decision == "REJECT" else (1, 9))
            assert gateway.calls == (0 if decision == "REJECT" else 1)
            events = [event.event_type for event in await repo.list_events(cid)]
            assert events[0] == "CREATED"
            if decision == "REJECT":
                assert events == ["CREATED", "REJECTED"]
            else:
                assert events.count("APPROVED") == 1
                assert events.count("CONSUMED") == 1
                assert events.count("EXECUTION_SUCCEEDED") == 1
                assert (
                    events.index("APPROVED")
                    < events.index("CONSUMED")
                    < events.index("EXECUTION_SUCCEEDED")
                )
    finally:
        gateway.db.close()
        await db.close()
