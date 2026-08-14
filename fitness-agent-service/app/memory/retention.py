"""Memory 正文保留期限和不可逆脱敏 Worker。

生命周期状态和审计事件是业务追踪信息，不能因为正文清理而一起删除。这个仓储只在
PostgreSQL 内执行有界、可并行的终态数据治理：正式 Memory 将 content 替换为脱敏标记，
候选 Memory 将密文清空，并在同一事务写入 REDACTED 审计事件。Worker 不读取或解密正文。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.infrastructure.database import Database


@dataclass(frozen=True)
class MemoryRetentionRunResult:
    """一次保留期限清理的结果。"""

    memories_redacted: int
    candidates_redacted: int


class MemoryRetentionRepository:
    """按保留期限清理正式 Memory 和候选正文。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def redact_due(self, *, limit: int = 500) -> MemoryRetentionRunResult:
        """批量脱敏到期终态正文，使用 ``SKIP LOCKED`` 支持多实例并行。"""

        if limit < 1 or limit > 5000:
            raise ValueError("memory retention batch size must be between 1 and 5000")
        memory_statement = text(
            """
            WITH due AS (
                SELECT id
                FROM agent_memories
                WHERE status IN ('REVOKED', 'EXPIRED')
                  AND content_redacted = false
                  AND retention_until IS NOT NULL
                  AND retention_until <= CURRENT_TIMESTAMP
                ORDER BY retention_until, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_memories AS memory
            SET content = CAST(:redacted_content AS JSONB),
                content_redacted = true,
                redacted_at = CURRENT_TIMESTAMP,
                redaction_reason = 'RETENTION_EXPIRED',
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE memory.id = due.id
            RETURNING memory.id, memory.subject_user_id, memory.organization_id,
                      memory.status, memory.version
            """
        )
        candidate_statement = text(
            """
            WITH due AS (
                SELECT id
                FROM agent_memory_candidates
                WHERE status IN ('APPROVED', 'REJECTED', 'EXPIRED')
                  AND payload_redacted = false
                  AND retention_until IS NOT NULL
                  AND retention_until <= CURRENT_TIMESTAMP
                ORDER BY retention_until, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_memory_candidates AS candidate
            SET payload_ciphertext = :empty_payload,
                payload_redacted = true,
                payload_redacted_at = CURRENT_TIMESTAMP,
                payload_redaction_reason = 'RETENTION_EXPIRED',
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE candidate.id = due.id
            RETURNING candidate.id, candidate.subject_user_id, candidate.organization_id,
                      candidate.status, candidate.payload_hash
            """
        )
        async with self._database.engine.begin() as connection:
            memory_rows = (
                (
                    await connection.execute(
                        memory_statement,
                        {
                            "limit": limit,
                            "redacted_content": '{"redacted": true}',
                        },
                    )
                )
                .mappings()
                .all()
            )
            for row in memory_rows:
                operation_id = f"memory-redaction:{row['id']}:{int(row['version'])}"
                await connection.execute(
                    text(
                        """
                        INSERT INTO agent_memory_events (
                            memory_id, subject_user_id, organization_id, event_type,
                            actor_type, actor_user_id, status_after, version_after,
                            request_id, operation_id
                        ) VALUES (
                            :memory_id, :subject_user_id, :organization_id, 'REDACTED',
                            'SYSTEM', NULL, :status_after, :version_after,
                            :request_id, :operation_id
                        )
                        ON CONFLICT (operation_id) DO NOTHING
                        """
                    ),
                    {
                        "memory_id": str(row["id"]),
                        "subject_user_id": str(row["subject_user_id"]),
                        "organization_id": str(row["organization_id"]),
                        "status_after": str(row["status"]),
                        "version_after": int(row["version"]),
                        "request_id": operation_id,
                        "operation_id": operation_id,
                    },
                )
            candidate_rows = (
                (
                    await connection.execute(
                        candidate_statement,
                        {"limit": limit, "empty_payload": b""},
                    )
                )
                .mappings()
                .all()
            )
            for row in candidate_rows:
                candidate_id = str(row["id"])
                operation_id = f"memory-candidate-redaction:{candidate_id}"
                await connection.execute(
                    text(
                        """
                        INSERT INTO agent_memory_candidate_events (
                            candidate_id, subject_user_id, organization_id, event_type,
                            actor_type, actor_user_id, status_after, request_id,
                            decision_request_id, payload_hash
                        ) VALUES (
                            :candidate_id, :subject_user_id, :organization_id, 'REDACTED',
                            'SYSTEM', NULL, :status_after, :request_id,
                            NULL, :payload_hash
                        )
                        """
                    ),
                    {
                        "candidate_id": candidate_id,
                        "subject_user_id": str(row["subject_user_id"]),
                        "organization_id": str(row["organization_id"]),
                        "status_after": str(row["status"]),
                        "request_id": operation_id,
                        "payload_hash": str(row["payload_hash"]),
                    },
                )
        return MemoryRetentionRunResult(
            memories_redacted=len(memory_rows), candidates_redacted=len(candidate_rows)
        )
