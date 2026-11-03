"""真实 PG：批准事务回滚和并发重试，必须使用隔离测试数据库。"""

from __future__ import annotations

import asyncio
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
from app.memory.candidate_repository import MemoryCandidateRepository
from app.memory.repository import MemoryRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="仅在隔离 PostgreSQL 中启用",
)


@pytest.mark.asyncio
async def test_approval_rollback_and_concurrent_idempotent_retries(monkeypatch):
    database = Database(Settings(_env_file=None))
    candidates = MemoryCandidateRepository(database, AesGcmPayloadCipher(b"7" * 32, "test"))
    memories = MemoryRepository(database)
    suffix = uuid4().hex
    subject, org = f"atomic-user-{suffix}", f"atomic-org-{suffix}"
    identity = AgentIdentity(
        subject=subject,
        organization_ids=frozenset({org}),
        roles=frozenset(),
        issued_at=1,
        expires_at=2,
    )
    candidate_id = None
    try:
        candidate = await candidates.create_pending(
            identity=identity,
            organization_id=org,
            candidate=MemoryCandidate(
                memory_type="SCHEDULE_PREFERENCE", memory_key="training_time", value="周末上午"
            ),
            source_thread_id="thread",
            source_request_id="source",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        candidate_id = candidate.id
        original_event = candidates._insert_event

        async def fail_approved_event(*args, **kwargs):
            if kwargs.get("event_type") == "APPROVED":
                raise RuntimeError("injected failure after memory write")
            await original_event(*args, **kwargs)

        monkeypatch.setattr(candidates, "_insert_event", fail_approved_event)
        with pytest.raises(RuntimeError, match="injected failure"):
            await candidates.approve_atomic(
                candidate_id,
                identity=identity,
                decision_request_id="decision",
                memory_repository=memories,
            )
        async with database.engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM agent_memories WHERE subject_user_id=:subject"),
                    {"subject": subject},
                )
            ).scalar_one()
            status = (
                await connection.execute(
                    text("SELECT status FROM agent_memory_candidates WHERE id=:id"),
                    {"id": candidate_id},
                )
            ).scalar_one()
            assert count == 0 and status == "PENDING"
        monkeypatch.setattr(candidates, "_insert_event", original_event)
        results = await asyncio.gather(
            *(
                candidates.approve_atomic(
                    candidate_id,
                    identity=identity,
                    decision_request_id="decision",
                    memory_repository=memories,
                )
                for _ in range(5)
            )
        )
        assert len({memory.id for _, memory in results}) == 1
        assert all(
            record.status == "APPROVED" and memory.version == 1 for record, memory in results
        )
        async with database.engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM agent_memories WHERE subject_user_id=:subject"),
                    {"subject": subject},
                )
            ).scalar_one()
            events = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM agent_memory_candidate_events "
                        "WHERE candidate_id=:id AND event_type='APPROVED'"
                    ),
                    {"id": candidate_id},
                )
            ).scalar_one()
            assert count == events == 1
    finally:
        async with database.engine.begin() as connection:
            for table in (
                "agent_memory_events",
                "agent_memories",
                "agent_memory_candidate_events",
                "agent_notification_outbox",
                "agent_memory_candidates",
                "agent_memory_profiles",
            ):
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE subject_user_id=:subject"),
                    {"subject": subject},
                )
        await database.close()
