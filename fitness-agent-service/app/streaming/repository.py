from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.database import Database
from app.streaming.completion import HistoryCommit, TurnCompletion, publish_summary

if TYPE_CHECKING:
    from app.memory.source import MemorySourceRepository


class RunConflict(RuntimeError):
    """相同任务 ID 被换绑请求或权限范围。"""


class RunSessionBusy(RuntimeError):
    """同一会话的上一运行尚未退出。"""


@dataclass(frozen=True)
class ChatRun:
    run_id: str
    status: str
    result: dict[str, Any] | None = None
    error_code: str | None = None
    stop_reason: str | None = None
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None


def request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ChatRunRepository:
    """仅存范围摘要和加密终态，签名身份及输入正文不持久化。"""

    def __init__(
        self,
        database: Database,
        cipher: AesGcmPayloadCipher,
        *,
        timeout: float,
        retention_hours: int,
        memory_sources: MemorySourceRepository | None = None,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.timeout = timeout
        self.retention_hours = retention_hours
        self.memory_sources = memory_sources

    async def claim(
        self, run_id: str, owner: str, digest: str, *, scope: str | None = None
    ) -> tuple[ChatRun, bool]:
        async with self.database.engine.begin() as connection:
            await self._expire(connection, [run_id], scope=scope)
            inserted = await connection.execute(
                text("""
                INSERT INTO agent_chat_runs
                  (run_id, owner_hash, request_digest, conversation_scope_hash,
                   execution_deadline, expires_at)
                VALUES (:id, :owner, :digest, :scope,
                  CURRENT_TIMESTAMP + :timeout * INTERVAL '1 second',
                  CURRENT_TIMESTAMP + :retention * INTERVAL '1 hour')
                ON CONFLICT DO NOTHING RETURNING run_id
            """),
                {
                    "id": run_id,
                    "owner": owner,
                    "digest": digest,
                    "scope": scope,
                    "timeout": self.timeout + 15,
                    "retention": self.retention_hours,
                },
            )
            created = inserted.first() is not None
            result = await connection.execute(
                text("""
                SELECT * FROM agent_chat_runs WHERE run_id=:id AND owner_hash=:owner
                  AND expires_at > CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner},
            )
            row = result.mappings().first()
            if row is None and scope is not None:
                active = await connection.execute(
                    text("""
                    SELECT 1 FROM agent_chat_runs WHERE conversation_scope_hash=:scope
                    AND status IN ('RUNNING','CANCEL_REQUESTED')
                """),
                    {"scope": scope},
                )
                if active.first() is not None:
                    raise RunSessionBusy("上一任务正在执行或停止")
            if row is None or row["request_digest"] != digest:
                raise RunConflict("任务 ID 已被使用")
            return self._decode(dict(row)), created

    async def get(self, run_id: str, owner: str) -> ChatRun | None:
        async with self.database.engine.begin() as connection:
            # 进程失联后标记未知，永不自动重跑可能产生副作用的原请求。
            await self._expire(connection, [run_id])
            result = await connection.execute(
                text("""
                SELECT * FROM agent_chat_runs WHERE run_id=:id AND owner_hash=:owner
                  AND expires_at > CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner},
            )
            row = result.mappings().first()
            return self._decode(dict(row)) if row else None

    async def history_commit(
        self, thread_id: str, owner: str, subject: str, prior_run_id: str | None = None
    ) -> HistoryCommit | None:
        """可信 checkpoint 引用允许懒回填尚存的旧 COMPLETED；忽略响应 TTL。"""
        async with self.database.engine.begin() as connection:
            if prior_run_id:
                await connection.execute(
                    text("""
                    INSERT INTO agent_chat_history_commits
                      (thread_id,owner_hash,subject_user_id,run_id,version)
                    SELECT :thread,:owner,:subject,run_id,1 FROM agent_chat_runs
                    WHERE run_id=:run AND owner_hash=:owner AND status='COMPLETED'
                      AND (conversation_scope_hash IS NULL OR conversation_scope_hash=:scope)
                    ON CONFLICT(thread_id,owner_hash) DO NOTHING
                """),
                    {
                        "thread": thread_id,
                        "owner": owner,
                        "subject": subject,
                        "run": prior_run_id,
                        "scope": request_digest({"owner": owner, "thread": thread_id}),
                    },
                )
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT run_id,version FROM agent_chat_history_commits
                WHERE thread_id=:thread AND owner_hash=:owner AND subject_user_id=:subject
            """),
                        {"thread": thread_id, "owner": owner, "subject": subject},
                    )
                )
                .mappings()
                .first()
            )
            return HistoryCommit(str(row["run_id"]), int(row["version"])) if row else None

    async def _expire(
        self, connection: Any, run_ids: list[str], *, scope: str | None = None
    ) -> None:
        await connection.execute(
            text("""
            UPDATE agent_chat_runs SET status='UNKNOWN',
              error_code=CASE WHEN status='CANCEL_REQUESTED'
                THEN 'CANCEL_ACK_TIMEOUT' ELSE 'EXECUTION_LOST' END,
              stop_reason=COALESCE(stop_reason,'EXECUTION_LOST'), updated_at=CURRENT_TIMESTAMP
            WHERE (run_id=ANY(CAST(:ids AS TEXT[])) OR conversation_scope_hash=:scope)
              AND ((status='RUNNING' AND execution_deadline < CURRENT_TIMESTAMP)
               OR (status='CANCEL_REQUESTED' AND cancel_deadline < CURRENT_TIMESTAMP))
        """),
            {"ids": run_ids, "scope": scope},
        )

    async def request_cancel(self, run_id: str, owner: str, request_id: str) -> ChatRun | None:
        async with self.database.engine.begin() as connection:
            await self._expire(connection, [run_id])
            await connection.execute(
                text("""
                UPDATE agent_chat_runs SET status='CANCEL_REQUESTED',
                  cancel_request_id=:request, cancel_requested_at=CURRENT_TIMESTAMP,
                  cancel_deadline=CURRENT_TIMESTAMP + INTERVAL '10 seconds',
                  stop_reason='USER_CANCELLED', updated_at=CURRENT_TIMESTAMP
                WHERE run_id=:id AND owner_hash=:owner AND status='RUNNING'
                  AND expires_at > CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner, "request": request_id},
            )
        return await self.get(run_id, owner)

    async def controls(self, run_ids: list[str]) -> dict[str, ChatRun]:
        if not run_ids:
            return {}
        async with self.database.engine.begin() as connection:
            await self._expire(connection, run_ids)
            rows = await connection.execute(
                text("""
                SELECT run_id, status, error_code, stop_reason, cancel_requested_at, cancelled_at
                FROM agent_chat_runs WHERE run_id=ANY(CAST(:ids AS TEXT[]))
            """),
                {"ids": run_ids},
            )
            return {row["run_id"]: ChatRun(**dict(row)) for row in rows.mappings()}

    async def finish_cancelled(self, run_id: str, owner: str) -> bool:
        async with self.database.engine.begin() as connection:
            changed = await connection.execute(
                text("""
                UPDATE agent_chat_runs SET status='CANCELLED', cancelled_at=CURRENT_TIMESTAMP,
                  updated_at=CURRENT_TIMESTAMP
                WHERE run_id=:id AND owner_hash=:owner AND status='CANCEL_REQUESTED'
                RETURNING run_id
            """),
                {"id": run_id, "owner": owner},
            )
            return changed.first() is not None

    async def finish(
        self,
        run_id: str,
        owner: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        stop_reason: str | None = None,
        completion: TurnCompletion | None = None,
    ) -> bool:
        if status not in {"COMPLETED", "FAILED", "UNKNOWN"}:
            raise ValueError("无效任务终态")
        ciphertext = (
            self.cipher.encrypt(
                json.dumps(result, ensure_ascii=False).encode(),
                associated_data=f"chat-run:{run_id}:{owner}",
            )
            if result is not None
            else None
        )
        async with self.database.engine.begin() as connection:
            updated = await connection.execute(
                text("""
                UPDATE agent_chat_runs SET status=:status, result_ciphertext=:cipher,
                    key_version=:version, error_code=:error, stop_reason=:reason,
                    updated_at=CURRENT_TIMESTAMP
                WHERE run_id=:id AND owner_hash=:owner AND status='RUNNING'
                RETURNING run_id
            """),
                {
                    "id": run_id,
                    "owner": owner,
                    "status": status,
                    "cipher": ciphertext,
                    "version": self.cipher.key_version if ciphertext is not None else None,
                    "error": error_code,
                    "reason": stop_reason,
                },
            )
            if updated.first() is None:
                return False
            if status == "COMPLETED" and completion is not None:
                params = {
                    "thread": completion.thread_id,
                    "owner": owner,
                    "subject": completion.subject_user_id,
                    "run": run_id,
                    "base": completion.base_version,
                }
                if completion.base_version == 0:
                    committed = await connection.execute(
                        text("""
                        INSERT INTO agent_chat_history_commits
                          (thread_id,owner_hash,subject_user_id,run_id,version)
                        VALUES (:thread,:owner,:subject,:run,1)
                        ON CONFLICT(thread_id,owner_hash) DO NOTHING RETURNING version
                    """),
                        params,
                    )
                else:
                    committed = await connection.execute(
                        text("""
                        UPDATE agent_chat_history_commits SET run_id=:run,version=version+1,
                          updated_at=CURRENT_TIMESTAMP
                        WHERE thread_id=:thread AND owner_hash=:owner AND subject_user_id=:subject
                          AND version=:base RETURNING version
                    """),
                        params,
                    )
                if committed.first() is None:
                    raise RunConflict("会话完成版本已变化")
                if completion.summary is not None:
                    if (completion.summary.thread_id, completion.summary.subject_user_id) != (
                        completion.thread_id,
                        completion.subject_user_id,
                    ):
                        raise RunConflict("摘要范围不匹配")
                    # 摘要是可丢弃的派生数据：记忆版本变化时放弃旧摘要，保留已完成对话。
                    await publish_summary(
                        connection, completion.summary, run_id, skip_stale_memory=True
                    )
                if completion.memory_source is not None and self.memory_sources is not None:
                    await self.memory_sources.complete_on_connection(
                        connection, completion.memory_source
                    )
            return True

    def _decode(self, row: dict[str, Any]) -> ChatRun:
        result = None
        if row["result_ciphertext"] is not None:
            result = json.loads(
                self.cipher.decrypt(
                    bytes(row["result_ciphertext"]),
                    associated_data=f"chat-run:{row['run_id']}:{row['owner_hash']}",
                    key_version=row["key_version"],
                )
            )
        return ChatRun(
            row["run_id"],
            row["status"],
            result,
            row["error_code"],
            row.get("stop_reason"),
            row.get("cancel_requested_at"),
            row.get("cancelled_at"),
        )

    async def cleanup_history(
        self, existing_threads: Callable[[list[str]], Awaitable[set[str]]], *, batch_size: int = 500
    ) -> int:
        """只有 checkpoint 确认不存在才回收凭据；查询故障不删除。"""
        async with self.database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text("""
                SELECT thread_id,owner_hash,run_id,version FROM agent_chat_history_commits
                WHERE checked_at < CURRENT_TIMESTAMP-INTERVAL '1 hour'
                ORDER BY checked_at LIMIT :batch
            """),
                        {"batch": batch_size},
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return 0
        existing = await existing_threads([str(row["thread_id"]) for row in rows])
        deleted = 0
        async with self.database.engine.begin() as connection:
            for row in rows:
                params = dict(row)
                if row["thread_id"] in existing:
                    await connection.execute(
                        text("""
                        UPDATE agent_chat_history_commits SET checked_at=CURRENT_TIMESTAMP
                        WHERE thread_id=:thread_id AND owner_hash=:owner_hash AND version=:version
                    """),
                        params,
                    )
                else:
                    params["scope"] = request_digest(
                        {"owner": row["owner_hash"], "thread": row["thread_id"]}
                    )
                    changed = await connection.execute(
                        text("""
                        DELETE FROM agent_chat_history_commits
                        WHERE thread_id=:thread_id AND owner_hash=:owner_hash AND version=:version
                        AND NOT EXISTS (SELECT 1 FROM agent_chat_runs WHERE conversation_scope_hash=:scope
                          AND status IN ('RUNNING','CANCEL_REQUESTED')) RETURNING thread_id
                    """),
                        params,
                    )
                    if changed.first() is not None:
                        deleted += 1
        return deleted

    async def cleanup(self, *, batch_size: int = 500) -> int:
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""
                DELETE FROM agent_chat_runs WHERE run_id IN (
                  SELECT run_id FROM agent_chat_runs WHERE expires_at < CURRENT_TIMESTAMP
                  ORDER BY expires_at LIMIT :batch FOR UPDATE SKIP LOCKED
                ) RETURNING run_id
            """),
                {"batch": batch_size},
            )
            return len(result.all())
