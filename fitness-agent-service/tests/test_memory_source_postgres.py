"""只允许在隔离测试库开启：验证真实 PG 约束、任务屏障与来源清理。"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.source import MemorySource, MemorySourceConflict, MemorySourceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MEMORY_SOURCE_PG_TESTS") != "1",
    reason="仅在隔离 PostgreSQL 中设置 AGENT_RUN_MEMORY_SOURCE_PG_TESTS=1",
)


def make_identity(subject: str, organization: str) -> AgentIdentity:
    return AgentIdentity(
        subject=subject,
        organization_ids=frozenset({organization}),
        roles=frozenset(),
        issued_at=1,
        expires_at=2,
    )


@pytest.mark.asyncio
async def test_capture_dedup_conflict_scope_and_epoch_barrier():
    db = Database(Settings())
    repository = MemorySourceRepository(db, AesGcmPayloadCipher(b"1" * 32, "test"))
    suffix = uuid4().hex
    subject, org = f"source-user-{suffix}", f"source-org-{suffix}"
    user = make_identity(subject, org)
    other = make_identity(f"other-{suffix}", org)
    try:
        first = await repository.capture(
            identity=user, thread_id="thread", source_request_id="request", message="我通常周末训练"
        )
        assert first is not None
        again = await repository.capture(
            identity=user, thread_id="thread", source_request_id="request", message="我通常周末训练"
        )
        assert again == first
        with pytest.raises(MemorySourceConflict):
            await repository.capture(
                identity=user, thread_id="thread", source_request_id="request", message="不同输入"
            )
        separate = await repository.capture(
            identity=other, thread_id="thread", source_request_id="request", message="不同用户"
        )
        assert separate is not None and separate.id != first.id
        assert await repository.complete(first) is True
        assert await repository.complete(first) is True
        forged = MemorySource(
            first.id,
            other.subject,
            org,
            first.thread_id,
            first.source_request_id,
            first.write_epoch,
        )
        assert await repository.complete(forged) is False
        async with db.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("SELECT * FROM agent_memory_source_messages WHERE id=:id"),
                        {"id": first.id},
                    )
                )
                .mappings()
                .one()
            )
            assert b"\xe5\x91\xa8\xe6\x9c\xab" not in bytes(row["message_ciphertext"])
            assert row["subject_user_id"] == subject and row["organization_id"] == org
            jobs = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM agent_memory_extraction_jobs WHERE source_message_id=:id"
                    ),
                    {"id": first.id},
                )
            ).scalar_one()
            assert jobs == 1
            await connection.execute(
                text(
                    "UPDATE agent_memory_profiles SET write_epoch=write_epoch+1 WHERE subject_user_id=:s AND organization_id=:o"
                ),
                {"s": subject, "o": org},
            )
        assert await repository.complete(first) is False
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memory_profiles SET extraction_enabled=false WHERE subject_user_id=:s AND organization_id=:o"
                ),
                {"s": subject, "o": org},
            )
        assert (
            await repository.capture(
                identity=user, thread_id="thread", source_request_id="next", message="不应存储"
            )
            is None
        )
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_memory_source_messages WHERE subject_user_id IN (:s,:other)"
                ),
                {"s": subject, "other": other.subject},
            )
            await connection.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id IN (:s,:other)"),
                {"s": subject, "other": other.subject},
            )
        await db.close()


@pytest.mark.asyncio
async def test_cleanup_expired_cancels_running_job_and_skips_locked_work():
    db = Database(Settings())
    repository = MemorySourceRepository(db, AesGcmPayloadCipher(b"1" * 32, "test"))
    suffix = uuid4().hex
    subject, org = f"source-user-{suffix}", f"source-org-{suffix}"
    user = make_identity(subject, org)
    source = None
    try:
        source = await repository.capture(
            identity=user, thread_id="thread", source_request_id="request", message="我喜欢深蹲"
        )
        assert source is not None
        assert await repository.complete(source)
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memory_source_messages SET retain_until=CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id=:id"
                ),
                {"id": source.id},
            )
            await connection.execute(
                text(
                    "UPDATE agent_memory_extraction_jobs SET status='RUNNING',lease_token='old',lease_until=CURRENT_TIMESTAMP + INTERVAL '5 minutes' WHERE source_message_id=:id"
                ),
                {"id": source.id},
            )
        async with db.engine.begin() as holder:
            await holder.execute(
                text(
                    "SELECT id FROM agent_memory_extraction_jobs WHERE source_message_id=:id FOR UPDATE"
                ),
                {"id": source.id},
            )
            await repository.cleanup_expired(limit=100)
            exists = (
                await holder.execute(
                    text("SELECT count(*) FROM agent_memory_source_messages WHERE id=:id"),
                    {"id": source.id},
                )
            ).scalar_one()
            assert exists == 1
        await repository.cleanup_expired(limit=100)
        async with db.engine.begin() as connection:
            exists = (
                await connection.execute(
                    text("SELECT count(*) FROM agent_memory_source_messages WHERE id=:id"),
                    {"id": source.id},
                )
            ).scalar_one()
            jobs = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM agent_memory_extraction_jobs WHERE source_message_id=:id"
                    ),
                    {"id": source.id},
                )
            ).scalar_one()
            assert exists == jobs == 0
        assert await repository.complete(source) is False
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_memory_source_messages WHERE subject_user_id=:s"),
                {"s": subject},
            )
            await connection.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id=:s"), {"s": subject}
            )
        await db.close()
