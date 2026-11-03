"""长期健身 Memory 的 PostgreSQL 持久化。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
    validate_memory_owner,
)


class MemoryNotFoundError(LookupError):
    """指定主体范围内不存在目标 Memory。"""


class MemoryVersionConflictError(RuntimeError):
    """Memory 版本已变化，调用方必须重新读取后再操作。"""


@dataclass(frozen=True)
class MemorySettings:
    extraction_enabled: bool = True
    personalization_enabled: bool = True
    revision: int = 0


class MemoryRepository:
    """使用参数化 SQL 保存 Memory，并把主体范围放进每条查询条件。

    Memory 是 Agent 自己的结构化数据，允许由 Python 服务写入 PostgreSQL；训练计划等
    业务事实仍然只能通过 Java Gateway 写入 MySQL。这里不使用向量字段，因为少量用户偏好
    的精确过滤和版本控制比语义近邻检索更适合企业权限边界。
    """

    def __init__(self, database: Database, *, terminal_retention_days: int = 90) -> None:
        if terminal_retention_days < 1 or terminal_retention_days > 3650:
            raise ValueError("Memory 终态保留期限必须在 1 到 3650 天之间")
        self._database = database
        self._terminal_retention_days = terminal_retention_days

    async def get_settings(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> MemorySettings:
        """读取本人机构设置；不存在的 profile 等同于默认设置，不产生写入。"""
        validate_memory_owner(identity, organization_id)
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT extraction_enabled, personalization_enabled, revision
                FROM agent_memory_profiles WHERE subject_user_id=:subject AND organization_id=:org
            """),
                        {"subject": identity.subject, "org": organization_id},
                    )
                )
                .mappings()
                .first()
            )
        return MemorySettings(**dict(row)) if row is not None else MemorySettings()

    async def update_settings(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        expected_revision: int,
        extraction_enabled: bool | None,
        personalization_enabled: bool | None,
    ) -> MemorySettings:
        """按版本更新设置，并在同一事务内使旧来源、候选和摘要失效。"""
        validate_memory_owner(identity, organization_id)
        async with self._database.engine.begin() as connection:
            profile = await self.lock_profile_on_connection(
                connection, subject_user_id=identity.subject, organization_id=organization_id
            )
            if int(profile["revision"]) != expected_revision:
                raise MemoryVersionConflictError("Memory 设置已变更，请刷新后重试")
            new_extraction = (
                bool(profile["extraction_enabled"])
                if extraction_enabled is None
                else extraction_enabled
            )
            new_personalization = (
                bool(profile["personalization_enabled"])
                if personalization_enabled is None
                else personalization_enabled
            )
            if new_extraction != bool(profile["extraction_enabled"]) or new_personalization != bool(
                profile["personalization_enabled"]
            ):
                await connection.execute(
                    text("""
                    UPDATE agent_memory_profiles SET extraction_enabled=:extraction,
                    personalization_enabled=:personalization
                    WHERE subject_user_id=:subject AND organization_id=:org
                """),
                    {
                        "extraction": new_extraction,
                        "personalization": new_personalization,
                        "subject": identity.subject,
                        "org": organization_id,
                    },
                )
                await self._bump_profile(connection, identity.subject, organization_id, forget=True)
                expected_revision += 1
            return MemorySettings(new_extraction, new_personalization, expected_revision)

    @staticmethod
    async def lock_profile_on_connection(
        connection: Any, *, subject_user_id: str, organization_id: str
    ) -> Any:
        """创建并锁定主体机构的串行化屏障；必须与调用方共用事务。"""
        params = {"subject": subject_user_id, "org": organization_id}
        # 全部记忆写入遵守 subject fence -> profile -> 摘要/记忆 的顺序。
        await connection.execute(
            text("""
            INSERT INTO agent_memory_subject_fences (subject_user_id)
            VALUES (:subject) ON CONFLICT DO NOTHING
        """),
            params,
        )
        await connection.execute(
            text("""
            SELECT revision FROM agent_memory_subject_fences
            WHERE subject_user_id=:subject FOR UPDATE
        """),
            params,
        )
        await connection.execute(
            text("""
            INSERT INTO agent_memory_profiles (subject_user_id, organization_id)
            VALUES (:subject, :org) ON CONFLICT DO NOTHING
        """),
            params,
        )
        return (
            (
                await connection.execute(
                    text("""
            SELECT * FROM agent_memory_profiles
            WHERE subject_user_id = :subject AND organization_id = :org FOR UPDATE
        """),
                    params,
                )
            )
            .mappings()
            .one()
        )

    @staticmethod
    async def _bump_profile(
        connection: Any, subject: str, org: str, *, forget: bool = False
    ) -> None:
        # 与 Memory 更新同一事务完成；调用方已持有主体 fence。
        await connection.execute(
            text("""
            UPDATE agent_memory_subject_fences SET revision=revision+1
            WHERE subject_user_id=:subject
        """),
            {"subject": subject},
        )
        await connection.execute(
            text("""
            DELETE FROM agent_session_summaries WHERE subject_user_id=:subject
        """),
            {"subject": subject},
        )
        await connection.execute(
            text("""
            UPDATE agent_memory_profiles SET revision = revision + 1,
                write_epoch = write_epoch + :forget, updated_at = CURRENT_TIMESTAMP
            WHERE subject_user_id = :subject AND organization_id = :org
        """),
            {"subject": subject, "org": org, "forget": int(forget)},
        )

    async def apply_candidate_on_connection(
        self,
        connection: Any,
        *,
        identity: AgentIdentity,
        organization_id: str,
        candidate: Any,
        operation_id: str,
        expected_epoch: int | None = None,
    ) -> FitnessMemory:
        """在候选的审批事务内执行受限变更；调用方负责锁候选并更新其状态。"""
        from .registry import normalize_identity
        from .service import _normalize_memory_payload

        validate_memory_owner(identity, organization_id)
        if not operation_id.strip():
            raise MemoryVersionConflictError("缺少操作标识")
        profile = await self.lock_profile_on_connection(
            connection, subject_user_id=identity.subject, organization_id=organization_id
        )
        if expected_epoch is not None and int(profile["write_epoch"]) != expected_epoch:
            raise MemoryVersionConflictError("记忆已经撤销或清空，候选需要重新确认")
        action = getattr(candidate, "action", None) or "ADD"
        if action not in {"ADD", "UPDATE", "NOOP"}:
            raise MemoryVersionConflictError("候选需要先澄清")
        memory_type, _, content, _ = _normalize_memory_payload(
            memory_type=candidate.memory_type,
            memory_key=candidate.memory_key,
            value=candidate.value,
            unit=candidate.unit,
            expires_at=None,
        )
        slot = getattr(candidate, "slot_code", None)
        scope_key, item_key = (
            normalize_identity(memory_type, slot, candidate.value) if slot else (None, None)
        )
        params = {
            "subject": identity.subject,
            "org": organization_id,
            "slot": slot,
            "scope": scope_key,
            "item": item_key,
            "type": memory_type,
            "key": candidate.memory_key,
            "target": getattr(candidate, "target_memory_id", None),
        }
        if action in {"UPDATE", "NOOP"}:
            if not params["target"] or not getattr(candidate, "expected_version", None):
                raise MemoryVersionConflictError("修改必须指定目标和版本")
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT * FROM agent_memories WHERE id = :target AND subject_user_id = :subject
                AND organization_id = :org AND status = 'ACTIVE'
                AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) FOR UPDATE
            """),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or int(row["version"]) != candidate.expected_version
                or row["memory_type"] != memory_type
                or (slot is not None and row["slot_code"] != slot)
            ):
                raise MemoryVersionConflictError("Memory 不存在、已撤销或版本已变更")
            if action == "NOOP":
                return memory_from_row(row)
            if slot is not None and row["scope_key"] != scope_key:
                raise MemoryVersionConflictError("记忆适用范围已改变，需重新确认")
            if slot is not None and row["item_key"] != item_key:
                conflict = (
                    (
                        await connection.execute(
                            text("""
                    SELECT id FROM agent_memories WHERE subject_user_id = :subject
                    AND organization_id = :org AND slot_code = :slot AND scope_key = :scope
                    AND item_key = :item AND status = 'ACTIVE' AND id <> :target FOR UPDATE
                """),
                            params,
                        )
                    )
                    .mappings()
                    .first()
                )
                if conflict:
                    raise MemoryVersionConflictError("修改后的记忆已存在，需重新确认")
            content["key"] = row["memory_key"]
            if slot is None:
                params["item"] = row["item_key"]
            params.update(
                {
                    "content": json.dumps(content, ensure_ascii=False),
                    "op": operation_id,
                    "version": candidate.expected_version,
                }
            )
            row = (
                (
                    await connection.execute(
                        text("""
                UPDATE agent_memories SET content = CAST(:content AS JSONB), version = version + 1,
                    item_key = :item,
                    source_request_id = :op, updated_at = CURRENT_TIMESTAMP
                WHERE id = :target AND subject_user_id = :subject AND organization_id = :org
                    AND status = 'ACTIVE' AND version = :version RETURNING *
            """),
                        params,
                    )
                )
                .mappings()
                .first()
            )
        else:
            # 包括已过期但尚未清扫的项；不能为绕过静态唯一约束而重活旧项。
            existing = (
                (
                    await connection.execute(
                        text("""
                SELECT id FROM agent_memories WHERE subject_user_id = :subject
                AND organization_id = :org AND (
                    (CAST(:slot AS TEXT) IS NOT NULL AND status = 'ACTIVE' AND slot_code = :slot AND scope_key = :scope AND item_key = :item)
                    OR (CAST(:slot AS TEXT) IS NULL AND memory_type = :type AND memory_key = :key)
                ) FOR UPDATE
            """),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                raise MemoryVersionConflictError("相关记忆已存在，需重新比较并确认")
            # 旧版全量唯一键仍保留，使用随机操作身份避免终态复用冲突。
            generated_key = (
                "m_" + hashlib.sha256(operation_id.encode()).hexdigest()[:60]
                if slot
                else candidate.memory_key
            )
            content["key"] = generated_key
            params.update(
                {
                    "id": str(uuid4()),
                    "key": generated_key,
                    "op": operation_id,
                    "content": json.dumps(content, ensure_ascii=False),
                }
            )
            row = (
                (
                    await connection.execute(
                        text("""
                INSERT INTO agent_memories (id, subject_user_id, organization_id, memory_type,
                    memory_key, content, source_type, confidence, status, version, source_request_id,
                    slot_code, scope_key, item_key)
                VALUES (:id, :subject, :org, :type, :key, CAST(:content AS JSONB),
                    'USER_EXPLICIT', 1.0, 'ACTIVE', 1, :op, :slot, :scope, :item) RETURNING *
            """),
                        params,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise MemoryVersionConflictError("Memory 已发生变化")
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
            request_id=operation_id,
            operation_id=operation_id,
        )
        await self._bump_profile(connection, identity.subject, organization_id)
        return memory_from_row(row)

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
        """新增明确确认的记忆；已存在的键需要版本化纠正，不能盲目覆盖。"""

        statement = text(
            """
            INSERT INTO agent_memories (
                id, subject_user_id, organization_id, memory_type, memory_key, content,
                source_type, confidence, status, version, expires_at, source_request_id,
                retention_until, content_redacted, redacted_at, redaction_reason
            ) VALUES (
                :id, :subject_user_id, :organization_id, :memory_type, :memory_key,
                CAST(:content AS JSONB), 'USER_EXPLICIT', 1.0, 'ACTIVE', 1,
                :expires_at, :source_request_id, NULL, false, NULL, NULL
            )
            ON CONFLICT (subject_user_id, organization_id, memory_type, memory_key)
            DO NOTHING
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
            SELECT memory_id, request_fingerprint FROM agent_memory_events
            WHERE event_type = 'SAVED' AND operation_id = :operation_id
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
        fingerprint = _request_fingerprint(
            "save", identity.subject, organization_id, memory_type, memory_key, content, expires_at
        )
        async with self._database.engine.begin() as connection:
            await self.lock_profile_on_connection(
                connection, subject_user_id=identity.subject, organization_id=organization_id
            )
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
                _check_fingerprint(previous_event, fingerprint)
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
                    raise RuntimeError("Memory 审计事件指向不存在的记录")
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
                raise MemoryVersionConflictError("Memory 已存在，请先读取并按版本纠正")
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
                    request_fingerprint=fingerprint,
                )
                await self._bump_profile(connection, identity.subject, organization_id)
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
            SELECT memory_id, request_fingerprint FROM agent_memory_events
            WHERE event_type = 'SAVED' AND operation_id = :operation_id
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
                item_key = :item_key,
                expires_at = :expires_at,
                source_request_id = :source_request_id,
                retention_until = NULL,
                content_redacted = false,
                redacted_at = NULL,
                redaction_reason = NULL,
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
            "item_key": None,
        }
        fingerprint = _request_fingerprint(
            "correct",
            identity.subject,
            organization_id,
            memory_id,
            expected_version,
            content,
            expires_at,
        )
        async with self._database.engine.begin() as connection:
            await self.lock_profile_on_connection(
                connection, subject_user_id=identity.subject, organization_id=organization_id
            )
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
                _check_fingerprint(previous_event, fingerprint)
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
                    raise RuntimeError("Memory 审计事件指向不存在的记录")
                return memory_from_row(row)
            existing = (await connection.execute(memory_by_id_statement, params)).mappings().first()
            if existing is None:
                raise MemoryVersionConflictError("Memory 不存在、已撤销或版本已变更")
            params["item_key"] = existing["item_key"]
            if existing["slot_code"]:
                from .registry import normalize_identity

                _, params["item_key"] = normalize_identity(
                    existing["memory_type"], existing["slot_code"], str(content["value"])
                )
                conflict = (
                    (
                        await connection.execute(
                            text("""
                    SELECT id FROM agent_memories WHERE subject_user_id = :subject_user_id
                    AND organization_id = :organization_id AND slot_code = :slot
                    AND scope_key = :scope AND item_key = :item_key
                    AND status = 'ACTIVE' AND id <> :memory_id FOR UPDATE
                """),
                            {
                                **params,
                                "slot": existing["slot_code"],
                                "scope": existing["scope_key"],
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if conflict:
                    raise MemoryVersionConflictError("修改后的记忆已存在，需重新确认")
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
                    _check_fingerprint(previous_event, fingerprint)
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
                raise MemoryVersionConflictError("Memory 不存在、已撤销或版本已变更")
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
                request_fingerprint=fingerprint,
            )
            await self._bump_profile(connection, identity.subject, organization_id)
        return memory_from_row(row)

    async def list_active(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        personalization_only: bool = False,
    ) -> list[FitnessMemory]:
        """只读取本人、指定机构且尚未过期的 Memory。"""

        statement = text(
            """
            SELECT * FROM agent_memories
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND status = 'ACTIVE'
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
              AND (NOT :personalization_only OR COALESCE((
                  SELECT personalization_enabled FROM agent_memory_profiles profile
                  WHERE profile.subject_user_id = :subject_user_id
                    AND profile.organization_id = :organization_id
              ), true))
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
                            "personalization_only": personalization_only,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [memory_from_row(row) for row in rows]

    async def list_training_context_for_authorized_subject(
        self, *, subject_user_id: str, organization_id: str
    ) -> list[FitnessMemory]:
        """读取已由 Gateway 授权的目标学员训练白名单，不包含沟通等无关偏好。"""

        if not subject_user_id.strip() or not organization_id.strip():
            raise ValueError("目标学员和机构不能为空")
        statement = text(
            """
            SELECT * FROM agent_memories
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND memory_type IN (
                  'TRAINING_GOAL', 'TRAINING_PREFERENCE',
                  'EQUIPMENT_AVAILABILITY', 'SCHEDULE_PREFERENCE'
              )
              AND status = 'ACTIVE'
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
              AND COALESCE((SELECT personalization_enabled FROM agent_memory_profiles profile
                  WHERE profile.subject_user_id = :subject_user_id
                    AND profile.organization_id = :organization_id), true)
            ORDER BY memory_type, memory_key, updated_at DESC
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {"subject_user_id": subject_user_id, "organization_id": organization_id},
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
            SET status = 'REVOKED', version = version + 1,
                retention_until = CURRENT_TIMESTAMP + (:retention_days * INTERVAL '1 day'),
                updated_at = CURRENT_TIMESTAMP
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
            SELECT memory_id, request_fingerprint FROM agent_memory_events
            WHERE event_type = 'REVOKED' AND operation_id = :operation_id
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
        fingerprint = _request_fingerprint(
            "revoke", identity.subject, organization_id, memory_id, expected_version
        )
        async with self._database.engine.begin() as connection:
            await self.lock_profile_on_connection(
                connection, subject_user_id=identity.subject, organization_id=organization_id
            )
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
                _check_fingerprint(previous_event, fingerprint)
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
                    raise RuntimeError("Memory 审计事件指向不存在的记录")
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
                            "retention_days": self._terminal_retention_days,
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
                    _check_fingerprint(previous_event, fingerprint)
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
                raise MemoryVersionConflictError("Memory 不存在、已撤销或版本已变更")
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
                request_fingerprint=fingerprint,
            )
            await self._bump_profile(connection, identity.subject, organization_id, forget=True)
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
            raise MemoryNotFoundError("未找到 Memory")
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
            raise ValueError("Memory 事件数量限制必须在 1 到 100 之间")
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
            UPDATE agent_memories AS memory
            SET status = 'EXPIRED', version = version + 1,
                retention_until = CURRENT_TIMESTAMP + (:retention_days * INTERVAL '1 day'),
                updated_at = CURRENT_TIMESTAMP
            WHERE memory.id IN :ids AND memory.status = 'ACTIVE'
                AND memory.expires_at IS NOT NULL AND memory.expires_at <= CURRENT_TIMESTAMP
            RETURNING memory.*
            """
        ).bindparams(bindparam("ids", expanding=True))
        async with self._database.engine.begin() as connection:
            # 获取候选时不锁 Memory；所有事务按 profile -> Memory 顺序加锁，避免死锁。
            due = (
                (
                    await connection.execute(
                        text("""
                SELECT id, subject_user_id, organization_id FROM agent_memories
                WHERE status = 'ACTIVE' AND expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP
                ORDER BY expires_at, id LIMIT :limit
            """),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            owners = sorted(
                {(str(row["subject_user_id"]), str(row["organization_id"])) for row in due}
            )
            for subject, org in owners:
                await self.lock_profile_on_connection(
                    connection, subject_user_id=subject, organization_id=org
                )
            rows = (
                (
                    await connection.execute(
                        statement,
                        {
                            "ids": [str(row["id"]) for row in due],
                            "retention_days": self._terminal_retention_days,
                        },
                    )
                )
                .mappings()
                .all()
            )
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
            for subject, org in sorted(
                {(str(row["subject_user_id"]), str(row["organization_id"])) for row in rows}
            ):
                await self._bump_profile(connection, subject, org, forget=True)
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
        request_fingerprint: str | None = None,
    ) -> None:
        """在状态变更的同一事务中追加审计事件；重复操作只保留一条事件。"""

        await connection.execute(
            text(
                """
                INSERT INTO agent_memory_events (
                    memory_id, subject_user_id, organization_id, event_type, actor_type,
                    actor_user_id, status_after, version_after, request_id, operation_id, request_fingerprint
                ) VALUES (
                    :memory_id, :subject_user_id, :organization_id, :event_type, :actor_type,
                    :actor_user_id, :status_after, :version_after, :request_id, :operation_id, :request_fingerprint
                )
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
                "request_fingerprint": request_fingerprint,
            },
        )


def _request_fingerprint(*parts: Any) -> str:
    """用于检测幂等键被重复用于不同命令；不记录原始请求。"""
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _check_fingerprint(event: Any, expected: str) -> None:
    if event["request_fingerprint"] != expected:
        raise MemoryVersionConflictError("操作标识已用于其他请求，或旧操作无法安全验证")


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
        content_redacted=bool(row.get("content_redacted", False)),
        slot_code=row.get("slot_code"),
        scope_key=row.get("scope_key"),
        item_key=row.get("item_key"),
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
        raise RuntimeError("必需的时间戳为 NULL")
    return normalized
