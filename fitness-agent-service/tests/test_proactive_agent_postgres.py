"""需要显式开启的主动式 Agent PostgreSQL 闭环契约测试。"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository, _from_row
from app.notifications.worker import NotificationOutboxWorker
from app.proactive.candidate_repository import ProactiveCandidateRepository
from app.proactive.candidate_repository import _from_row as candidate_from_row
from app.proactive.candidates import CandidateType, candidate_expiry
from app.proactive.decision_agent import deterministic_fallback

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


@pytest.mark.asyncio
async def test_candidate_decision_snapshot_outbox_and_delivery_are_atomic() -> None:
    database = Database(Settings())
    candidates = ProactiveCandidateRepository()
    outbox = NotificationOutboxRepository()
    token = uuid4().hex
    subject = f"proactive-postgres-{token}"
    candidate_id: str | None = None
    outbox_id: str | None = None
    try:
        async with database.engine.begin() as connection:
            candidate_id = await candidates.enqueue(
                connection,
                candidate_type=CandidateType.TRAINING_ADHERENCE.value,
                reason_code="TRAINING_INACTIVE",
                subject_user_id=subject,
                organization_id="proactive-postgres-org",
                aggregate_type="TRAINING_PLAN",
                aggregate_id=f"plan-{token}",
                trigger_source="SCHEDULE",
                trigger_event_ids=(),
                facts={"inactive_days": 3},
                dedupe_key=f"proactive-postgres:{token}",
                expires_at=candidate_expiry(CandidateType.TRAINING_ADHERENCE),
            )
        assert candidate_id is not None

        async with database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """UPDATE agent_proactive_candidates
                               SET status='PROCESSING', locked_by=:worker,
                                   locked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                               WHERE id=:id RETURNING *"""
                        ),
                        {"worker": token, "id": candidate_id},
                    )
                )
                .mappings()
                .one()
            )
        candidate = candidate_from_row(row)
        output = deterministic_fallback(candidate)

        async with database.engine.begin() as connection:
            await candidates.save_context_snapshot(
                connection,
                candidate_id=candidate.id,
                context={"recent_events": [], "published_notifications_last_7d": 0},
                context_version="test-v1",
            )
            await candidates.save_decision(
                connection,
                candidate=candidate,
                output=output,
                model_name="test-model",
                prompt_version="test-prompt",
                policy_version="test-policy",
                input_digest="input-digest",
                output_digest="output-digest",
                safety_status="FALLBACK",
                input_tokens=None,
                output_tokens=None,
            )
            snapshot = await candidates.create_message_snapshot(
                connection,
                candidate=candidate,
                output=output,
                template_key="TRAINING_ADHERENCE_SUPPORT",
                cta_type="RESUME_TRAINING",
                content_source="FALLBACK",
            )
            await outbox.enqueue_on_connection(
                connection,
                notification_type="TRAINING_ADHERENCE_SUPPORT",
                subject_user_id=subject,
                organization_id=candidate.organization_id,
                aggregate_type=candidate.aggregate_type,
                aggregate_id=candidate.aggregate_id,
                dedupe_key=f"proactive-agent:{candidate.id}:IN_APP",
                payload={"candidate_id": candidate.id, "message_snapshot_id": snapshot.id},
            )
            assert await candidates.complete(
                connection,
                candidate_id=candidate.id,
                worker_id=token,
                status="NOTIFICATION_QUEUED",
            )
            row = (
                (
                    await connection.execute(
                        text(
                            """UPDATE agent_notification_outbox
                               SET status='PROCESSING', locked_by=:worker, locked_at=CURRENT_TIMESTAMP
                               WHERE dedupe_key=:dedupe RETURNING *"""
                        ),
                        {"worker": token, "dedupe": f"proactive-agent:{candidate.id}:IN_APP"},
                    )
                )
                .mappings()
                .one()
            )
            record = _from_row(row)
            outbox_id = record.id

        worker = NotificationOutboxWorker(
            database,
            outbox,
            worker_id=token,
            proactive_candidates=candidates,
        )
        assert await worker._publish_one(record) == "published"

        async with database.engine.connect() as connection:
            candidate_status = await connection.scalar(
                text("SELECT status FROM agent_proactive_candidates WHERE id=:id"),
                {"id": candidate.id},
            )
            notification_count = await connection.scalar(
                text(
                    """SELECT COUNT(*) FROM agent_in_app_notifications
                       WHERE proactive_candidate_id=:id AND subject_user_id=:subject"""
                ),
                {"id": candidate.id, "subject": subject},
            )
        assert candidate_status == "COMPLETED"
        assert notification_count == 1
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_in_app_notifications WHERE subject_user_id=:subject"),
                {"subject": subject},
            )
            if outbox_id is not None:
                await connection.execute(
                    text("DELETE FROM agent_notification_delivery_attempts WHERE outbox_id=:id"),
                    {"id": outbox_id},
                )
                await connection.execute(
                    text("DELETE FROM agent_notification_outbox WHERE id=:id"),
                    {"id": outbox_id},
                )
            if candidate_id is not None:
                await connection.execute(
                    text("DELETE FROM agent_proactive_candidates WHERE id=:id"),
                    {"id": candidate_id},
                )
        await database.close()


@pytest.mark.asyncio
async def test_dead_candidate_operations_are_scoped_to_admin_organizations() -> None:
    database = Database(Settings())
    candidates = ProactiveCandidateRepository()
    token = uuid4().hex
    candidate_ids: list[str] = []
    try:
        async with database.engine.begin() as connection:
            for organization_id in ("scope-org-a", "scope-org-b"):
                candidate_id = await candidates.enqueue(
                    connection,
                    candidate_type=CandidateType.TRAINING_ADHERENCE.value,
                    reason_code="TRAINING_INACTIVE",
                    subject_user_id=f"scope-user-{token}",
                    organization_id=organization_id,
                    aggregate_type="TRAINING_PLAN",
                    aggregate_id=f"scope-plan-{token}",
                    trigger_source="SCHEDULE",
                    trigger_event_ids=(),
                    facts={"inactive_days": 3},
                    dedupe_key=f"scope:{organization_id}:{token}",
                    expires_at=candidate_expiry(CandidateType.TRAINING_ADHERENCE),
                )
                assert candidate_id is not None
                candidate_ids.append(candidate_id)
            await connection.execute(
                text(
                    "UPDATE agent_proactive_candidates SET status='DEAD' "
                    "WHERE id = ANY(CAST(:ids AS TEXT[]))"
                ),
                {"ids": candidate_ids},
            )

        async with database.engine.begin() as connection:
            scoped = await candidates.list_dead(
                connection, organization_ids=("scope-org-a",), limit=20
            )
            assert [item.id for item in scoped if item.id in candidate_ids] == [candidate_ids[0]]
            assert not await candidates.replay_dead(
                connection,
                candidate_id=candidate_ids[1],
                operator_id="organization-admin",
                reason="scope test",
                organization_ids=("scope-org-a",),
            )
            assert await candidates.replay_dead(
                connection,
                candidate_id=candidate_ids[0],
                operator_id="organization-admin",
                reason="scope test",
                organization_ids=("scope-org-a",),
            )
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_proactive_candidates WHERE id = ANY(CAST(:ids AS TEXT[]))"),
                {"ids": candidate_ids},
            )
        await database.close()
