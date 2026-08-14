"""需要显式开启的正式 Memory PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.repository import MemoryRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_memory_repository_writes_audit_and_revoke_is_idempotent() -> None:
    database = Database(Settings(_env_file=None))
    repository = MemoryRepository(database)
    suffix = str(uuid4())
    identity = AgentIdentity(
        subject=f"memory-test-{suffix}",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    try:
        first = await repository.save(
            identity=identity,
            organization_id="org-1",
            memory_type="TRAINING_PREFERENCE",
            memory_key="preferred_style",
            content={"key": "preferred_style", "value": "力量训练"},
            expires_at=None,
            source_request_id=f"memory-save:{suffix}:1",
        )
        retried = await repository.save(
            identity=identity,
            organization_id="org-1",
            memory_type="TRAINING_PREFERENCE",
            memory_key="preferred_style",
            content={"key": "preferred_style", "value": "错误重试内容"},
            expires_at=None,
            source_request_id=f"memory-save:{suffix}:1",
        )
        assert retried.version == first.version
        assert retried.content["value"] == "力量训练"

        corrected = await repository.save(
            identity=identity,
            organization_id="org-1",
            memory_type="TRAINING_PREFERENCE",
            memory_key="preferred_style",
            content={"key": "preferred_style", "value": "自重训练"},
            expires_at=None,
            source_request_id=f"memory-save:{suffix}:2",
        )
        assert corrected.version == 2

        revoked = await repository.revoke(
            identity=identity,
            organization_id="org-1",
            memory_id=corrected.id,
            expected_version=corrected.version,
            source_request_id=f"memory-revoke:{suffix}:1",
        )
        retried_revoke = await repository.revoke(
            identity=identity,
            organization_id="org-1",
            memory_id=corrected.id,
            expected_version=corrected.version,
            source_request_id=f"memory-revoke:{suffix}:1",
        )
        assert revoked.status == "REVOKED"
        assert retried_revoke.version == revoked.version

        events = await repository.list_events(corrected.id, identity=identity)
        assert [(event.event_type, event.status_after) for event in events] == [
            ("SAVED", "ACTIVE"),
            ("SAVED", "ACTIVE"),
            ("REVOKED", "REVOKED"),
        ]

        expiring = await repository.save(
            identity=identity,
            organization_id="org-1",
            memory_type="SCHEDULE_PREFERENCE",
            memory_key="training_time",
            content={"key": "training_time", "value": "周二晚上"},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            source_request_id=f"memory-save:{suffix}:3",
        )
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memories "
                    "SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' "
                    "WHERE id = :id"
                ),
                {"id": expiring.id},
            )
        assert await repository.expire_due(limit=10) == 1
        expiry_events = await repository.list_events(expiring.id, identity=identity)
        assert [event.event_type for event in expiry_events] == ["SAVED", "EXPIRED"]
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_memory_events WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
            await connection.execute(
                text("DELETE FROM agent_memories WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
        await database.close()
