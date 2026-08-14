"""需要显式开启的 Memory 正文保留期限 PostgreSQL 契约测试。"""

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
from app.memory.candidate_repository import MemoryCandidateRepository, MemoryCandidateStateError
from app.memory.repository import MemoryRepository
from app.memory.retention import MemoryRetentionRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_memory_retention_redacts_content_but_keeps_audit() -> None:
    database = Database(Settings(_env_file=None))
    memory_repository = MemoryRepository(database, terminal_retention_days=1)
    candidate_repository = MemoryCandidateRepository(
        database,
        AesGcmPayloadCipher(key=b"2" * 32, key_version="retention-test-v1"),
        terminal_retention_days=1,
    )
    retention_repository = MemoryRetentionRepository(database)
    suffix = str(uuid4())
    subject = f"memory-retention-{suffix}"
    identity = AgentIdentity(
        subject=subject,
        organization_ids=frozenset({"org-retention"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    candidate_id: str | None = None
    memory_id: str | None = None
    try:
        memory = await memory_repository.save(
            identity=identity,
            organization_id="org-retention",
            memory_type="TRAINING_PREFERENCE",
            memory_key="preferred_style",
            content={"key": "preferred_style", "value": "力量训练"},
            expires_at=None,
            source_request_id=f"retention-memory-save:{suffix}",
        )
        memory_id = memory.id
        await memory_repository.revoke(
            identity=identity,
            organization_id="org-retention",
            memory_id=memory.id,
            expected_version=memory.version,
            source_request_id=f"retention-memory-revoke:{suffix}",
        )
        candidate = await candidate_repository.create_pending(
            identity=identity,
            organization_id="org-retention",
            candidate=MemoryCandidate(
                memory_type="SCHEDULE_PREFERENCE",
                memory_key="training_time",
                value="周二晚上",
            ),
            source_thread_id=f"retention-thread:{suffix}",
            source_request_id=f"retention-candidate-create:{suffix}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        candidate_id = candidate.id
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memory_candidates "
                    "SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE id = :id"
                ),
                {"id": candidate.id},
            )
        await candidate_repository.expire_due(limit=10)
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memories SET retention_until = CURRENT_TIMESTAMP - INTERVAL '1 minute' "
                    "WHERE id = :id"
                ),
                {"id": memory.id},
            )
            await connection.execute(
                text(
                    "UPDATE agent_memory_candidates SET retention_until = CURRENT_TIMESTAMP - INTERVAL '1 minute' "
                    "WHERE id = :id"
                ),
                {"id": candidate.id},
            )

        result = await retention_repository.redact_due(limit=10)
        assert result.memories_redacted == 1
        assert result.candidates_redacted == 1

        async with database.engine.connect() as connection:
            memory_row = (
                (
                    await connection.execute(
                        text("SELECT * FROM agent_memories WHERE id = :id"), {"id": memory.id}
                    )
                )
                .mappings()
                .one()
            )
            assert memory_row["content"] == {"redacted": True}
            assert memory_row["content_redacted"] is True
            candidate_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT payload_ciphertext, payload_redacted "
                            "FROM agent_memory_candidates WHERE id = :id"
                        ),
                        {"id": candidate.id},
                    )
                )
                .mappings()
                .one()
            )
            assert candidate_row["payload_ciphertext"] == b""
            assert candidate_row["payload_redacted"] is True

        memory_events = await memory_repository.list_events(memory.id, identity=identity)
        assert [event.event_type for event in memory_events][-1] == "REDACTED"
        candidate_events = await candidate_repository.list_events(candidate.id, identity=identity)
        assert [event.event_type for event in candidate_events][-1] == "REDACTED"
        with pytest.raises(MemoryCandidateStateError, match="redacted"):
            await candidate_repository.get_for_subject(candidate.id, identity=identity)
    finally:
        async with database.engine.begin() as connection:
            if candidate_id is not None:
                await connection.execute(
                    text("DELETE FROM agent_memory_candidate_events WHERE candidate_id = :id"),
                    {"id": candidate_id},
                )
                await connection.execute(
                    text("DELETE FROM agent_notification_outbox WHERE aggregate_id = :id"),
                    {"id": candidate_id},
                )
                await connection.execute(
                    text("DELETE FROM agent_memory_candidates WHERE id = :id"),
                    {"id": candidate_id},
                )
            if memory_id is not None:
                await connection.execute(
                    text("DELETE FROM agent_memory_events WHERE memory_id = :id"),
                    {"id": memory_id},
                )
                await connection.execute(
                    text("DELETE FROM agent_memories WHERE id = :id"),
                    {"id": memory_id},
                )
        await database.close()
