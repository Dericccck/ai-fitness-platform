"""长期健身 Memory 的 PostgreSQL 持久化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text

from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

from .models import (
    FitnessMemory,
    MemoryEventActorType,
    MemoryEventRecord,
    MemoryEventType,
    MemoryStatus,
    MemoryType,
)


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
        request_id: str | None = None,
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
            """
            SELECT * FROM agent_memories
            WHERE source_request_id = :source_request_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        previous_event_statement = text(
            """
            SELECT memory_id FROM agent_memory_events
            WHERE event_type = 'SAVED' AND operation_id = :operation_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        memory_by_id_statement = text(
            """
            SELECT * FROM agent_memories
            WHERE id = :memory_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
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
            # 先查不可变事件，而不是只查 agent_memories.source_request_id。后者会在同一
            # 稳定键被用户纠正时被新请求覆盖，无法防止很晚到达的旧请求再次改写 Memory。
            previous_event = (
                (
                    await connection.execute(
                        previous_event_statement,
                        {
                            "operation_id": source_request_id,
                            "subject_user_id": identity.subject,
                            "organization_id": organization_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if previous_event is not None:
                row = (
                    (
                        await connection.execute(
                            memory_by_id_statement,
                            {
                                "memory_id": previous_event["memory_id"],
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise RuntimeError("Memory audit event points to a missing row")
                return memory_from_row(row)
            # 先按确认执行请求做幂等读取，确保同一恢复请求不会把 Memory 版本再次加一。
            row = (await connection.execute(existing_request_statement, params)).mappings().first()
            is_new_operation = row is None
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
            if is_new_operation and row["source_request_id"] == source_request_id:
                await self._insert_event(
                    connection,
                    memory_id=str(row["id"]),
                    subject_user_id=identity.subject,
                    organization_id=organization_id,
                    event_type="SAVED",
                    actor_type="USER",
                    actor_user_id=identity.subject,
                    status_after="ACTIVE",
                    version_after=int(row["version"]),
                    request_id=request_id or source_request_id,
                    operation_id=source_request_id,
                )
        return memory_from_row(row)

    async def correct(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_id: str,
        expected_version: int,
        content: dict[str, Any],
        expires_at: datetime | None,
        source_request_id: str,
        request_id: str | None = None,
    ) -> FitnessMemory:
        """按具体 Memory 和乐观锁版本纠正内容，并追加一条 SAVED 事件。"""

        event_statement = text(
            """
            SELECT memory_id FROM agent_memory_events
            WHERE event_type = 'SAVED' AND operation_id = :operation_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        memory_by_id_statement = text(
            """
            SELECT * FROM agent_memories
            WHERE id = :memory_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        update_statement = text(
            """
            UPDATE agent_memories
            SET content = CAST(:content AS JSONB),
                status = 'ACTIVE',
                version = version + 1,
                expires_at = :expires_at,
                source_request_id = :source_request_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :memory_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND status = 'ACTIVE'
              AND version = :expected_version
            RETURNING *
            """
        )
        params = {
            "memory_id": memory_id,
            "subject_user_id": identity.subject,
            "organization_id": organization_id,
            "expected_version": expected_version,
            "content": json.dumps(content, ensure_ascii=False),
            "expires_at": expires_at,
            "source_request_id": source_request_id,
        }
        async with self._database.engine.begin() as connection:
            # 纠正请求可能因客户端超时重试；先查审计事件可以返回第一次成功结果，
            # 即使当前页面携带的 expected_version 已经不是最新版本。
            previous_event = (
                (
                    await connection.execute(
                        event_statement,
                        {
                            "operation_id": source_request_id,
                            "subject_user_id": identity.subject,
                            "organization_id": organization_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if previous_event is not None:
                row = (
                    (
                        await connection.execute(
                            memory_by_id_statement,
                            {
                                "memory_id": previous_event["memory_id"],
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise RuntimeError("Memory audit event points to a missing row")
                return memory_from_row(row)
            row = (await connection.execute(update_statement, params)).mappings().first()
            if row is None:
                # 与撤销一致，处理同一纠正请求的并发提交；其他版本冲突必须返回 409。
                previous_event = (
                    (
                        await connection.execute(
                            event_statement,
                            {
                                "operation_id": source_request_id,
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous_event is not None:
                    row = (
                        (
                            await connection.execute(
                                memory_by_id_statement,
                                {
                                    "memory_id": previous_event["memory_id"],
                                    "subject_user_id": identity.subject,
                                    "organization_id": organization_id,
                                },
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if row is not None:
                        return memory_from_row(row)
                raise MemoryVersionConflictError("memory is missing, revoked, or version changed")
            await self._insert_event(
                connection,
                memory_id=str(row["id"]),
                subject_user_id=identity.subject,
                organization_id=organization_id,
                event_type="SAVED",
                actor_type="USER",
                actor_user_id=identity.subject,
                status_after="ACTIVE",
                version_after=int(row["version"]),
                request_id=request_id or source_request_id,
                operation_id=source_request_id,
            )
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
        source_request_id: str,
        request_id: str | None = None,
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
        event_statement = text(
            """
            SELECT memory_id FROM agent_memory_events
            WHERE event_type = 'REVOKED' AND operation_id = :operation_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        memory_by_id_statement = text(
            """
            SELECT * FROM agent_memories
            WHERE id = :memory_id
              AND subject_user_id = :subject_user_id
              AND organization_id = :organization_id
            """
        )
        async with self._database.engine.begin() as connection:
            # HTTP 重试或 LangGraph 恢复重试先按操作幂等键收敛，不能仅依赖当前状态。
            previous_event = (
                (
                    await connection.execute(
                        event_statement,
                        {
                            "operation_id": source_request_id,
                            "subject_user_id": identity.subject,
                            "organization_id": organization_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if previous_event is not None:
                row = (
                    (
                        await connection.execute(
                            memory_by_id_statement,
                            {
                                "memory_id": previous_event["memory_id"],
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise RuntimeError("Memory audit event points to a missing row")
                return memory_from_row(row)
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
                # 并发的同一撤销请求可能在第一次查询后才提交。更新失败后再次查询事件，
                # 能把它识别为同一个已完成操作，而不是误报版本冲突。
                previous_event = (
                    (
                        await connection.execute(
                            event_statement,
                            {
                                "operation_id": source_request_id,
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous_event is not None:
                    row = (
                        (
                            await connection.execute(
                                memory_by_id_statement,
                                {
                                    "memory_id": previous_event["memory_id"],
                                    "subject_user_id": identity.subject,
                                    "organization_id": organization_id,
                                },
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if row is not None:
                        return memory_from_row(row)
                raise MemoryVersionConflictError("memory is missing, revoked, or version changed")
            await self._insert_event(
                connection,
                memory_id=str(row["id"]),
                subject_user_id=identity.subject,
                organization_id=organization_id,
                event_type="REVOKED",
                actor_type="USER",
                actor_user_id=identity.subject,
                status_after="REVOKED",
                version_after=int(row["version"]),
                request_id=request_id or source_request_id,
                operation_id=source_request_id,
            )
        return memory_from_row(row)

    async def get_for_subject(self, memory_id: str, *, identity: AgentIdentity) -> FitnessMemory:
        """读取本人且属于签名机构范围的 Memory；跨主体按不存在处理。"""

        statement = text(
            """
            SELECT * FROM agent_memories
            WHERE id = :memory_id
              AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "memory_id": memory_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise MemoryNotFoundError("memory not found")
        return memory_from_row(row)

    async def list_events(
        self,
        memory_id: str,
        *,
        identity: AgentIdentity,
        limit: int = 50,
    ) -> list[MemoryEventRecord]:
        """查询本人 Memory 的生命周期摘要，不返回正文或内部幂等参数。"""

        if limit < 1 or limit > 100:
            raise ValueError("memory event limit must be between 1 and 100")
        statement = text(
            """
            SELECT event.*
            FROM agent_memory_events AS event
            WHERE event.memory_id = :memory_id
              AND event.subject_user_id = :subject_user_id
              AND event.organization_id IN :organization_ids
            ORDER BY event.created_at, event.id
            LIMIT :limit
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        async with self._database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {
                            "memory_id": memory_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [memory_event_from_row(row) for row in rows]

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
            RETURNING memory.*
            """
        )
        async with self._database.engine.begin() as connection:
            rows = (await connection.execute(statement, {"limit": limit})).mappings().all()
            for row in rows:
                memory_id = str(row["id"])
                operation_id = f"memory-expiry:{memory_id}:{int(row['version'])}"
                await self._insert_event(
                    connection,
                    memory_id=memory_id,
                    subject_user_id=str(row["subject_user_id"]),
                    organization_id=str(row["organization_id"]),
                    event_type="EXPIRED",
                    actor_type="SYSTEM",
                    actor_user_id=None,
                    status_after="EXPIRED",
                    version_after=int(row["version"]),
                    request_id=operation_id,
                    operation_id=operation_id,
                )
        return len(rows)

    async def _insert_event(
        self,
        connection: Any,
        *,
        memory_id: str,
        subject_user_id: str,
        organization_id: str,
        event_type: MemoryEventType,
        actor_type: MemoryEventActorType,
        actor_user_id: str | None,
        status_after: MemoryStatus,
        version_after: int,
        request_id: str,
        operation_id: str,
    ) -> None:
        """在状态变更的同一事务中追加审计事件；重复操作只保留一条事件。"""

        await connection.execute(
            text(
                """
                INSERT INTO agent_memory_events (
                    memory_id, subject_user_id, organization_id, event_type, actor_type,
                    actor_user_id, status_after, version_after, request_id, operation_id
                ) VALUES (
                    :memory_id, :subject_user_id, :organization_id, :event_type, :actor_type,
                    :actor_user_id, :status_after, :version_after, :request_id, :operation_id
                )
                ON CONFLICT (operation_id) DO NOTHING
                """
            ),
            {
                "memory_id": memory_id,
                "subject_user_id": subject_user_id,
                "organization_id": organization_id,
                "event_type": event_type,
                "actor_type": actor_type,
                "actor_user_id": actor_user_id,
                "status_after": status_after,
                "version_after": version_after,
                "request_id": request_id,
                "operation_id": operation_id,
            },
        )


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
        created_at=_required_as_utc(row["created_at"]),
        updated_at=_required_as_utc(row["updated_at"]),
    )


def memory_event_from_row(row: Any) -> MemoryEventRecord:
    """把审计行映射为不带 SQLAlchemy 细节的摘要对象。"""

    return MemoryEventRecord(
        id=int(row["id"]),
        memory_id=str(row["memory_id"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        event_type=row["event_type"],
        actor_type=row["actor_type"],
        actor_user_id=str(row["actor_user_id"]) if row["actor_user_id"] else None,
        status_after=row["status_after"],
        version_after=int(row["version_after"]),
        request_id=str(row["request_id"]),
        operation_id=str(row["operation_id"]),
        created_at=_required_as_utc(row["created_at"]),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """把带时区时间统一成 UTC；可选过期时间的数据库 NULL 保持为 None。"""

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_as_utc(value: datetime) -> datetime:
    """转换数据库必填时间，并在 schema 异常时尽早失败。"""

    normalized = _as_utc(value)
    if normalized is None:
        raise RuntimeError("required timestamp is NULL")
    return normalized
