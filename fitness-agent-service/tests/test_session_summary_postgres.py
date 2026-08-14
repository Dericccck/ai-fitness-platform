"""需要显式开启的短期会话摘要 PostgreSQL 契约测试。"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.database import Database
from app.session_summary import SessionSummaryRepository, SessionSummaryScopeError

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_session_summary_is_subject_scoped_and_due_rows_are_deleted() -> None:
    database = Database(Settings(_env_file=None))
    repository = SessionSummaryRepository(database)
    cipher = AesGcmPayloadCipher(b"3" * 32, "session-summary-test-v1")
    suffix = str(uuid4())
    thread_id = f"fitness:summary-test-{suffix}"
    subject = f"summary-subject-{suffix}"
    summary = "用户希望进行力量训练。"
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()
    ciphertext = cipher.encrypt(
        summary.encode(),
        associated_data=f"fitness-session-summary:{thread_id}:{summary_hash}",
    )
    try:
        saved = await repository.upsert(
            thread_id=thread_id,
            subject_user_id=subject,
            summary_ciphertext=ciphertext,
            summary_key_version=cipher.key_version,
            summary_hash=summary_hash,
            message_count=12,
            retention_days=7,
        )
        assert saved.summary_version == 1
        assert (await repository.get_for_subject(thread_id, subject)) is not None
        assert await repository.get_for_subject(thread_id, "another-subject") is None

        with pytest.raises(SessionSummaryScopeError):
            await repository.upsert(
                thread_id=thread_id,
                subject_user_id="another-subject",
                summary_ciphertext=ciphertext,
                summary_key_version=cipher.key_version,
                summary_hash=summary_hash,
                message_count=13,
                retention_days=7,
            )

        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_session_summaries "
                    "SET retention_until = :expired WHERE thread_id = :thread_id"
                ),
                {
                    "expired": datetime.now(UTC) - timedelta(minutes=1),
                    "thread_id": thread_id,
                },
            )
        assert await repository.delete_due(limit=10) == 1
        assert await repository.get_for_subject(thread_id, subject) is None
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_session_summaries WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )
        await database.close()
