"""仅在显式指定的隔离 PG 执行真实事务、发布竞态与回滚验证。"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.database import Database
from app.memory.repository import MemoryRepository
from app.session_summary import SessionSummaryRepository
from app.streaming.completion import SummaryDraft, TurnCompletion, publish_summary
from app.streaming.repository import ChatRunRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MEMORY_SOURCE_PG_TESTS") != "1", reason="需要隔离的 PG 测试库"
)


@pytest.mark.asyncio
async def test_forget_deletes_summary_atomically_and_stale_draft_cannot_return():
    db = Database(Settings())
    memories = MemoryRepository(db)
    summaries = SessionSummaryRepository(db)
    runs = ChatRunRepository(
        db, AesGcmPayloadCipher(b"x" * 32, "test"), timeout=60, retention_hours=1
    )
    subject, org, thread, run = [str(uuid4()) for _ in range(4)]
    revision = await summaries.current_memory_revision(subject)
    draft = SummaryDraft(thread, subject, b"old", "test", "a" * 64, 12, 7, 0, "old", revision)
    try:
        await summaries.publish(draft)
        assert await summaries.get_for_subject(thread, subject) is not None
        async with db.engine.begin() as connection:
            await memories.lock_profile_on_connection(
                connection, subject_user_id=subject, organization_id=org
            )
            await memories._bump_profile(connection, subject, org, forget=True)
        assert await summaries.get_for_subject(thread, subject) is None
        assert await summaries.current_memory_revision(subject) == revision + 1
        # 完成流式对话时旧摘要被丢弃，但聊天提交仍能成功保存。
        await runs.claim(run, "owner", "digest")
        assert await runs.finish(
            run,
            "owner",
            status="COMPLETED",
            result={"answer": "ok"},
            completion=TurnCompletion(thread, subject, 0, draft),
        )
        assert await summaries.get_for_subject(thread, subject) is None
        assert await runs.history_commit(thread, "owner", subject) is not None
        async with db.engine.begin() as connection:
            with pytest.raises(RuntimeError, match="摘要版本失效"):
                await publish_summary(connection, draft, None)
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_session_summaries WHERE subject_user_id=:s"), {"s": subject}
            )
            await connection.execute(
                text("DELETE FROM agent_chat_history_commits WHERE subject_user_id=:s"),
                {"s": subject},
            )
            await connection.execute(
                text("DELETE FROM agent_chat_runs WHERE run_id=:r"), {"r": run}
            )
            await connection.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id=:s"), {"s": subject}
            )
            await connection.execute(
                text("DELETE FROM agent_memory_subject_fences WHERE subject_user_id=:s"),
                {"s": subject},
            )
        await db.close()


@pytest.mark.asyncio
async def test_memory_change_fences_concurrent_summary_and_rolls_back_together():
    db = Database(Settings())
    memories = MemoryRepository(db)
    summaries = SessionSummaryRepository(db)
    subject, org, thread = [str(uuid4()) for _ in range(3)]
    revision = await summaries.current_memory_revision(subject)
    draft = SummaryDraft(thread, subject, b"old", "test", "b" * 64, 12, 7, 0, "old", revision)
    locked = asyncio.Event()
    release = asyncio.Event()

    async def memory_write():
        async with db.engine.begin() as connection:
            await memories.lock_profile_on_connection(
                connection, subject_user_id=subject, organization_id=org
            )
            await memories._bump_profile(connection, subject, org, forget=True)
            locked.set()
            await release.wait()

    async def publish():
        async with db.engine.begin() as connection:
            return await publish_summary(connection, draft, None, skip_stale_memory=True)

    writer = None
    publisher = None
    try:
        # 首先证明回滚同时恢复版本及摘要正文。
        await summaries.publish(draft)
        with pytest.raises(RuntimeError, match="abort"):
            async with db.engine.begin() as connection:
                await memories.lock_profile_on_connection(
                    connection, subject_user_id=subject, organization_id=org
                )
                await memories._bump_profile(connection, subject, org, forget=True)
                raise RuntimeError("abort")
        assert await summaries.current_memory_revision(subject) == revision
        assert await summaries.get_for_subject(thread, subject) is not None
        writer = asyncio.create_task(memory_write())
        await asyncio.wait_for(locked.wait(), timeout=5)
        publisher = asyncio.create_task(publish())
        await asyncio.sleep(0.1)
        assert not publisher.done()
        release.set()
        await asyncio.wait_for(writer, timeout=5)
        assert await asyncio.wait_for(publisher, timeout=5) is False
        assert await summaries.get_for_subject(thread, subject) is None
    finally:
        release.set()
        for task in (writer, publisher):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_session_summaries WHERE subject_user_id=:s"), {"s": subject}
            )
            await connection.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id=:s"), {"s": subject}
            )
            await connection.execute(
                text("DELETE FROM agent_memory_subject_fences WHERE subject_user_id=:s"),
                {"s": subject},
            )
        await db.close()
