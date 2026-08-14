"""需要显式开启的 Memory 候选 PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.candidate import MemoryCandidate
from app.memory.candidate_repository import MemoryCandidateNotFound, MemoryCandidateRepository
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.worker import NotificationOutboxWorker

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_candidate_repository_encrypts_deduplicates_and_scopes_decisions() -> None:
    database = Database(Settings(_env_file=None))
    repository = MemoryCandidateRepository(
        database,
        AesGcmPayloadCipher(key=b"1" * 32, key_version="test-v1"),
    )
    candidate_id = str(uuid4())
    organization_id = f"candidate-test-org-{candidate_id}"
    identity = AgentIdentity(
        subject=f"candidate-test-{candidate_id}",
        organization_ids=frozenset({organization_id}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    candidate = MemoryCandidate(
        memory_type="TRAINING_PREFERENCE",
        memory_key="preferred_style",
        value="自重训练",
    )
    try:
        first = await repository.create_pending(
            identity=identity,
            organization_id=organization_id,
            candidate=candidate,
            source_thread_id="fitness:thread-hash",
            source_request_id="request-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        duplicate = await repository.create_pending(
            identity=identity,
            organization_id=organization_id,
            candidate=candidate,
            source_thread_id="fitness:thread-hash-2",
            source_request_id="request-2",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert duplicate.id == first.id
        assert duplicate.candidate.value == "自重训练"
        async with database.engine.begin() as connection:
            outbox_rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM agent_notification_outbox "
                            "WHERE aggregate_id = :candidate_id"
                        ),
                        {"candidate_id": first.id},
                    )
                )
                .mappings()
                .all()
            )
            assert len(outbox_rows) == 1
            assert outbox_rows[0]["status"] == "PENDING"
            assert outbox_rows[0]["payload"] == {"candidate_id": first.id}
        notification_worker = NotificationOutboxWorker(
            database,
            NotificationOutboxRepository(),
            worker_id="notification-test-worker",
        )
        worker_result = await notification_worker.run_once()
        assert (worker_result.claimed, worker_result.published, worker_result.retried) == (1, 1, 0)
        async with database.engine.begin() as connection:
            notification_rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM agent_in_app_notifications "
                            "WHERE aggregate_id = :candidate_id"
                        ),
                        {"candidate_id": first.id},
                    )
                )
                .mappings()
                .all()
            )
            assert len(notification_rows) == 1
            assert notification_rows[0]["status"] == "UNREAD"
            attempt_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT attempt.channel, attempt.attempt_no, attempt.status,
                                   attempt.provider_message_id
                            FROM agent_notification_delivery_attempts AS attempt
                            JOIN agent_notification_outbox AS outbox ON outbox.id = attempt.outbox_id
                            WHERE outbox.aggregate_id = :candidate_id
                            """
                        ),
                        {"candidate_id": first.id},
                    )
                )
                .mappings()
                .all()
            )
            assert [(row["channel"], row["attempt_no"], row["status"]) for row in attempt_rows] == [
                ("IN_APP", 1, "SUCCEEDED")
            ]
            assert attempt_rows[0]["provider_message_id"] == notification_rows[0]["id"]
            inbox = NotificationOutboxRepository()
            delivery_attempts = await inbox.list_delivery_attempts(
                connection,
                organization_id=organization_id,
                notification_type="MEMORY_CANDIDATE_PENDING",
                channel="IN_APP",
                status="SUCCEEDED",
                limit=10,
            )
            assert len(delivery_attempts) == 1
            assert delivery_attempts[0].attempt_no == 1
            assert delivery_attempts[0].organization_id == organization_id
            listed = await inbox.list_in_app(
                connection,
                subject_user_id=identity.subject,
                organization_id=organization_id,
                status="UNREAD",
                limit=10,
            )
            assert [notification.aggregate_id for notification in listed] == [first.id]
            marked = await inbox.mark_in_app_read(
                connection,
                notification_id=str(notification_rows[0]["id"]),
                subject_user_id=identity.subject,
                organization_ids=[organization_id],
            )
            assert marked is not None
            assert marked.status == "READ"
        created_events = await repository.list_events(first.id, identity=identity)
        assert [(event.event_type, event.actor_type) for event in created_events] == [
            ("CREATED", "AGENT")
        ]

        pending = await repository.list_pending(
            identity=identity,
            organization_id=organization_id,
            limit=10,
        )
        assert [record.id for record in pending] == [first.id]

        other_scope = AgentIdentity(
            subject=identity.subject,
            organization_ids=frozenset({"org-2"}),
            roles=identity.roles,
            issued_at=1,
            expires_at=2,
        )
        with pytest.raises(MemoryCandidateNotFound):
            await repository.get_for_subject(first.id, identity=other_scope)

        decided = await repository.decide(
            first.id,
            identity=identity,
            decision="APPROVED",
            decision_request_id="decision-1",
            now=datetime.now(UTC),
        )
        assert decided.status == "APPROVED"
        assert decided.decision_request_id == "decision-1"
        approved_events = await repository.list_events(first.id, identity=identity)
        assert [event.event_type for event in approved_events] == ["CREATED", "APPROVED"]

        expiring = await repository.create_pending(
            identity=identity,
                organization_id=organization_id,
            candidate=MemoryCandidate(
                memory_type="SCHEDULE_PREFERENCE",
                memory_key="training_time",
                value="周二晚上",
            ),
            source_thread_id="fitness:thread-hash",
            source_request_id="request-expiring",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_notification_delivery_attempts "
                    "WHERE outbox_id IN ("
                    "SELECT id FROM agent_notification_outbox WHERE subject_user_id = :subject"
                    ")"
                ),
                {"subject": identity.subject},
            )
            await connection.execute(
                text("DELETE FROM agent_notification_outbox WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
            await connection.execute(
                text(
                    "UPDATE agent_memory_candidates "
                    "SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE id = :id"
                ),
                {"id": expiring.id},
            )
        assert await repository.expire_due(limit=10) == 1
        expired = await repository.get_for_subject(expiring.id, identity=identity)
        assert expired.status == "EXPIRED"
        assert expired.decision_request_id == "system:expiry"
        expired_events = await repository.list_events(expiring.id, identity=identity)
        assert [event.event_type for event in expired_events] == ["CREATED", "EXPIRED"]
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_memory_candidate_events WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
            await connection.execute(
                text("DELETE FROM agent_memory_candidates WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
        await database.close()
