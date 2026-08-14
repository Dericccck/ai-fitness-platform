"""长期健身 Memory 的 PostgreSQL 持久化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

from .models import FitnessMemory, MemoryType


class MemoryNotFoundError(LookupError):
    """指定主体范围内不存在目标 Memory。"""


class MemoryVersionConflictError(RuntimeError):
    """Memory 版本已变化，调用方必须重新读取后再操作。"""


class MemoryRepository:
    """使用参数化 SQL 保存 Memory，并把主体范围放进每条查询条件。

    Memory 是 Agent 自己的结构化数据，允许由 Python 服务写入 PostgreSQL；训练计划等
    业务事实仍然只能通过 Java Gateway 写入 MySQL。这里不使用向量字段，因为少量用户偏好
    的精确过滤和版本控制比语义近邻检索更适合企业权限边界。
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_type: MemoryType,
        memory_key: str,
        content: dict[str, Any],
        expires_at: datetime | None,
        source_request_id: str,
    ) -> FitnessMemory:
        """按主体、机构、类型和键幂等覆盖 Memory，并递增业务版本。"""

        statement = text(
            """
            INSERT INTO agent_memories (
                id, subject_user_id, organization_id, memory_type, memory_key, content,
                source_type, confidence, status, version, expires_at, source_request_id
            ) VALUES (
                :id, :subject_user_id, :organization_id, :memory_type, :memory_key,
                CAST(:content AS JSONB), 'USER_EXPLICIT', 1.0, 'ACTIVE', 1,
                :expires_at, :source_request_id
            )
            ON CONFLICT (subject_user_id, organization_id, memory_type, memory_key)
            DO UPDATE SET
                content = EXCLUDED.content,
                source_type = EXCLUDED.source_type,
                confidence = EXCLUDED.confidence,
                status = 'ACTIVE',
                version = agent_memories.version + 1,
                expires_at = EXCLUDED.expires_at,
                source_request_id = EXCLUDED.source_request_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE agent_memories.source_request_id <> EXCLUDED.source_request_id
            RETURNING *
            """
        )
        existing_request_statement = text(
            "SELECT * FROM agent_memories WHERE source_request_id = :source_request_id"
        )
        params = {
            "id": str(uuid4()),
            "subject_user_id": identity.subject,
            "organization_id": organization_id,
            "memory_type": memory_type,
            "memory_key": memory_key,
            "content": json.dumps(content, ensure_ascii=False),
            "expires_at": expires_at,
            "source_request_id": source_request_id,
        }
        async with self._database.engine.begin() as connection:
            # 先按确认执行请求做幂等读取，确保同一恢复请求不会把 Memory 版本再次加一。
            row = (await connection.execute(existing_request_statement, params)).mappings().first()
            if row is None:
                row = (await connection.execute(statement, params)).mappings().first()
            if row is None:
                # 并发请求可能在第一次 SELECT 后才提交；上面的 UPSERT 会等待并因相同
                # source_request_id 不更新，此时重新读取即可收敛到同一行。
                row = (
                    (await connection.execute(existing_request_statement, params))
                    .mappings()
                    .first()
                )
            if row is None:
                raise RuntimeError("Memory idempotency write did not return a row")
        return memory_from_row(row)

    async def list_active(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
    ) -> list[FitnessMemory]:
        """只读取本人、指定机构且尚未过期的 Memory。"""

        statement = text(
            """
            SELECT * FROM agent_memories
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND status = 'ACTIVE'
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY memory_type, memory_key, updated_at DESC
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {
                            "subject_user_id": identity.subject,
                            "organization_id": organization_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [memory_from_row(row) for row in rows]

    async def revoke(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_id: str,
        expected_version: int,
    ) -> FitnessMemory:
        """按主体和版本撤销 Memory，防止确认期间覆盖了新版本。"""

        statement = text(
            """
            UPDATE agent_memories
            SET status = 'REVOKED', version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND status = 'ACTIVE'
              AND version = :expected_version
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "id": memory_id,
                            "subject_user_id": identity.subject,
                            "organization_id": organization_id,
                            "expected_version": expected_version,
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise MemoryVersionConflictError("memory is missing, revoked, or version changed")
        return memory_from_row(row)

    async def expire_due(self, *, limit: int = 500) -> int:
        """批量标记到期 Memory，供后续定时 Worker 调用。"""

        statement = text(
            """
            WITH due AS (
                SELECT id FROM agent_memories
                WHERE status = 'ACTIVE' AND expires_at IS NOT NULL
                  AND expires_at <= CURRENT_TIMESTAMP
                ORDER BY expires_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_memories AS memory
            SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE memory.id = due.id
            """
        )
        async with self._database.engine.begin() as connection:
            result = await connection.execute(statement, {"limit": limit})
        return result.rowcount or 0


def memory_from_row(row: Any) -> FitnessMemory:
    """把数据库行映射成不带 SQLAlchemy 细节的领域对象。"""

    content = row["content"]
    if isinstance(content, str):
        content = json.loads(content)
    return FitnessMemory(
        id=str(row["id"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        memory_type=row["memory_type"],
        memory_key=str(row["memory_key"]),
        content=dict(content),
        source_type=row["source_type"],
        confidence=float(row["confidence"]),
        status=row["status"],
        version=int(row["version"]),
        expires_at=_as_utc(row["expires_at"]),
        created_at=_as_utc(row["created_at"]),
        updated_at=_as_utc(row["updated_at"]),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
