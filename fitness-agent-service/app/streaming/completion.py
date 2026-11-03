"""仅在运行内部传递的完成事实与摘要候选，不进入响应或 checkpoint。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

if TYPE_CHECKING:
    from app.memory.source import MemorySource


@dataclass(frozen=True)
class HistoryCommit:
    run_id: str
    version: int


@dataclass(frozen=True)
class SummaryDraft:
    thread_id: str
    subject_user_id: str
    summary_ciphertext: bytes = field(repr=False)
    summary_key_version: str
    summary_hash: str
    message_count: int
    retention_days: int
    base_version: int
    plaintext: str = field(repr=False)
    memory_revision: int = 0


@dataclass
class TurnCompletion:
    thread_id: str
    subject_user_id: str
    base_version: int
    summary: SummaryDraft | None = None
    memory_source: MemorySource | None = None


async def publish_summary(
    connection: AsyncConnection,
    draft: SummaryDraft,
    source_run_id: str | None,
    *,
    skip_stale_memory: bool = False,
) -> bool:
    """持有会话提交行后按基准版本发布，冲突令整个完成事务回滚。"""
    params = {
        "thread": draft.thread_id,
        "subject": draft.subject_user_id,
        "cipher": draft.summary_ciphertext,
        "key": draft.summary_key_version,
        "hash": draft.summary_hash,
        "count": draft.message_count,
        "days": draft.retention_days,
        "base": draft.base_version,
        "source": source_run_id,
    }
    # Memory 变更和摘要发布都先锁主体栅栏。旧摘要在记忆变化后不能重新发布。
    await connection.execute(
        text("""
        INSERT INTO agent_memory_subject_fences (subject_user_id)
        VALUES (:subject) ON CONFLICT DO NOTHING
    """),
        params,
    )
    current = (
        await connection.execute(
            text("""
        SELECT revision FROM agent_memory_subject_fences
        WHERE subject_user_id=:subject FOR UPDATE
    """),
            params,
        )
    ).scalar_one()
    if int(current) != draft.memory_revision:
        if skip_stale_memory:
            return False
        raise RuntimeError("Memory 已变化，摘要版本失效")
    if draft.base_version == 0:
        saved = await connection.execute(
            text("""
            INSERT INTO agent_session_summaries
              (thread_id,subject_user_id,summary_ciphertext,summary_key_version,summary_hash,
               summary_version,message_count,retention_until,source_run_id)
            VALUES (:thread,:subject,:cipher,:key,:hash,1,:count,
              CURRENT_TIMESTAMP+:days*INTERVAL '1 day',:source)
            ON CONFLICT(thread_id) DO NOTHING RETURNING thread_id
        """),
            params,
        )
    else:
        saved = await connection.execute(
            text("""
            UPDATE agent_session_summaries SET summary_ciphertext=:cipher,summary_key_version=:key,
              summary_hash=:hash,summary_version=summary_version+1,message_count=:count,
              retention_until=CURRENT_TIMESTAMP+:days*INTERVAL '1 day',source_run_id=:source,
              updated_at=CURRENT_TIMESTAMP
            WHERE thread_id=:thread AND subject_user_id=:subject AND summary_version=:base
            RETURNING thread_id
        """),
            params,
        )
    if saved.first() is None:
        raise RuntimeError("会话摘要版本已变化")
    return True
