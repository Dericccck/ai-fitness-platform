"""需要显式开启的通知策略 PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.preferences import NotificationPreferenceRepository
from app.notifications.worker import NotificationOutboxWorker

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_notification_policy_publishes_defers_and_suppresses() -> None:
    database = Database(Settings(_env_file=None))
    outbox = NotificationOutboxRepository()
    preferences = NotificationPreferenceRepository()
    subjects = [f"notification-policy-{uuid4()}" for _ in range(3)]
    organization_id = "notification-policy-org"
    try:
        async with database.engine.begin() as connection:
            for subject, suffix in zip(subjects, ("disabled", "quiet", "default"), strict=True):
                await outbox.enqueue_on_connection(
                    connection,
                    notification_type="MEMORY_CANDIDATE_PENDING",
                    subject_user_id=subject,
                    organization_id=organization_id,
                    aggregate_type="memory_candidate",
                    aggregate_id=f"candidate-{suffix}-{uuid4()}",
                    dedupe_key=f"policy:{subject}",
                    payload={"candidate_id": f"candidate-{suffix}"},
                )
            await preferences.upsert(
                connection,
                subject_user_id=subjects[0],
                organization_id=organization_id,
                notification_type="MEMORY_CANDIDATE_PENDING",
                enabled=False,
                quiet_start=None,
                quiet_end=None,
                timezone="Asia/Shanghai",
                minimum_interval_seconds=0,
            )
            local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
            await preferences.upsert(
                connection,
                subject_user_id=subjects[1],
                organization_id=organization_id,
                notification_type="MEMORY_CANDIDATE_PENDING",
                enabled=True,
                quiet_start=(local_now - timedelta(minutes=1)).time(),
                quiet_end=(local_now + timedelta(minutes=30)).time(),
                timezone="Asia/Shanghai",
                minimum_interval_seconds=0,
            )

        worker = NotificationOutboxWorker(
            database,
            outbox,
            preferences=preferences,
            worker_id="notification-policy-test-worker",
        )
        result = await worker.run_once()
        assert result.claimed == 3
        assert result.published == 1
        assert result.deferred == 1
        assert result.suppressed == 1
        assert result.retried == 0

        async with database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT subject_user_id, status, suppression_reason
                            FROM agent_notification_outbox
                            WHERE subject_user_id = ANY(:subjects)
                            ORDER BY subject_user_id
                            """
                        ),
                        {"subjects": subjects},
                    )
                )
                .mappings()
                .all()
            )
            assert {row["status"] for row in rows} == {"PUBLISHED", "DEFERRED", "SUPPRESSED"}
            suppressed = next(row for row in rows if row["status"] == "SUPPRESSED")
            assert suppressed["suppression_reason"] == "USER_DISABLED"
            inbox_count = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM agent_in_app_notifications "
                    "WHERE subject_user_id = ANY(:subjects)"
                ),
                {"subjects": subjects},
            )
            assert inbox_count.scalar_one() == 1
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_notification_preferences "
                    "WHERE subject_user_id = ANY(:subjects)"
                ),
                {"subjects": subjects},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_in_app_notifications WHERE subject_user_id = ANY(:subjects)"
                ),
                {"subjects": subjects},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_notification_outbox WHERE subject_user_id = ANY(:subjects)"
                ),
                {"subjects": subjects},
            )
        await database.close()
