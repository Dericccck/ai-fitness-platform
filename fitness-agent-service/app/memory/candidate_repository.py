"""加密 Memory 候选的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import bindparam, text

from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.confirmation.normalization import canonical_json_bytes
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository

from .candidate import MemoryCandidate, MemoryCandidateEventRecord, MemoryCandidateRecord
from .models import validate_memory_owner


class MemoryCandidateNotFound(LookupError):
    """当前签名主体范围内找不到候选。"""


class MemoryCandidateStateError(RuntimeError):
    """候选当前状态不允许执行目标决定。"""


class MemoryCandidateRepository:
    """保存加密候选，并在每次读取/决定时强制主体和机构范围。"""

    def __init__(
        self,
        database: Database,
        cipher: AesGcmPayloadCipher,
        *,
        terminal_retention_days: int = 30,
    ) -> None:
        if terminal_retention_days < 1 or terminal_retention_days > 3650:
            raise ValueError("候选终态保留期限必须在 1 到 3650 天之间")
        self._database = database
        self._cipher = cipher
        self._terminal_retention_days = terminal_retention_days

    async def create_pending(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        candidate: MemoryCandidate,
        source_thread_id: str,
        source_request_id: str,
        expires_at: datetime,
    ) -> MemoryCandidateRecord:
        """创建或复用同一内容的待确认候选，避免重复弹出相同候选。"""

        validate_memory_owner(identity, organization_id)
        if not source_thread_id.strip() or not source_request_id.strip():
            raise MemoryCandidateStateError("必须提供候选来源标识")
        if expires_at <= datetime.now(UTC):
            raise MemoryCandidateStateError("候选过期时间必须在未来")
        payload = canonical_json_bytes(candidate.model_dump(mode="json", exclude_none=True))
        payload_hash = _sha256(payload)
        ciphertext = self._cipher.encrypt(payload, associated_data=payload_hash)
        statement = text(
            """
            INSERT INTO agent_memory_candidates (
                id, subject_user_id, organization_id, memory_type, memory_key,
                payload_hash, payload_ciphertext, payload_key_version,
                source_thread_id, source_request_id, status, expires_at
            ) VALUES (
                :id, :subject_user_id, :organization_id, :memory_type, :memory_key,
                :payload_hash, :payload_ciphertext, :payload_key_version,
                :source_thread_id, :source_request_id, 'PENDING', :expires_at
            )
            ON CONFLICT (subject_user_id, organization_id, memory_type, memory_key, payload_hash)
            WHERE status = 'PENDING'
            DO NOTHING
            RETURNING *
            """
        )
        existing = text(
            """
            SELECT * FROM agent_memory_candidates
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND memory_type = :memory_type
              AND memory_key = :memory_key
              AND payload_hash = :payload_hash
              AND status = 'PENDING'
            """
        )
        params = {
            "id": str(uuid4()),
            "subject_user_id": identity.subject,
            "organization_id": organization_id,
            "memory_type": candidate.memory_type,
            "memory_key": candidate.memory_key,
            "payload_hash": payload_hash,
            "payload_ciphertext": ciphertext,
            "payload_key_version": self._cipher.key_version,
            "source_thread_id": source_thread_id,
            "source_request_id": source_request_id,
            "expires_at": expires_at,
        }
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().first()
            if row is not None:
                await self._insert_event(
                    connection,
                    candidate_id=str(row["id"]),
                    subject_user_id=identity.subject,
                    organization_id=organization_id,
                    event_type="CREATED",
                    actor_type="AGENT",
                    actor_user_id=None,
                    status_after="PENDING",
                    request_id=source_request_id,
                    decision_request_id=None,
                    payload_hash=payload_hash,
                )
            else:
                row = (
                    (
                        await connection.execute(
                            existing,
                            {
                                "subject_user_id": identity.subject,
                                "organization_id": organization_id,
                                "memory_type": candidate.memory_type,
                                "memory_key": candidate.memory_key,
                                "payload_hash": payload_hash,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
            if row is not None:
                # 候选和通知任务必须在同一事务内提交。通知只携带候选 ID，不把解密后的
                # 用户偏好正文写进通用 Outbox；重复调用也能补齐历史候选缺失的通知任务。
                await NotificationOutboxRepository.enqueue_on_connection(
                    connection,
                    notification_type="MEMORY_CANDIDATE_PENDING",
                    subject_user_id=identity.subject,
                    organization_id=organization_id,
                    aggregate_type="memory_candidate",
                    aggregate_id=str(row["id"]),
                    dedupe_key=f"memory-candidate-pending:{row['id']}",
                    payload={"candidate_id": str(row["id"])},
                )
        if row is None:
            raise MemoryCandidateStateError("候选幂等写入没有返回记录")
        return self._record_from_row(row)

    async def list_events(
        self,
        candidate_id: str,
        *,
        identity: AgentIdentity,
        limit: int = 50,
    ) -> list[MemoryCandidateEventRecord]:
        """查询本人候选的不可变生命周期事件，不返回候选正文。"""

        if limit < 1 or limit > 100:
            raise ValueError("候选事件数量限制必须在 1 到 100 之间")
        statement = text(
            """
            SELECT event.*
            FROM agent_memory_candidate_events AS event
            WHERE event.candidate_id = :candidate_id
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
                            "candidate_id": candidate_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [event_from_row(row) for row in rows]

    async def list_pending(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        limit: int,
    ) -> list[MemoryCandidateRecord]:
        """查询本人未过期候选；过期候选不会继续出现在可操作列表。"""

        validate_memory_owner(identity, organization_id)
        statement = text(
            """
            SELECT * FROM agent_memory_candidates
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND status = 'PENDING'
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at, id
            LIMIT :limit
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
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [self._record_from_row(row) for row in rows]

    async def get_for_subject(
        self, candidate_id: str, *, identity: AgentIdentity
    ) -> MemoryCandidateRecord:
        """按候选 ID 和主体读取，跨主体时按不存在处理。"""

        statement = text(
            """
            SELECT * FROM agent_memory_candidates
            WHERE id = :id AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "id": candidate_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise MemoryCandidateNotFound("未找到 Memory 候选")
        if bool(row.get("payload_redacted", False)):
            raise MemoryCandidateStateError("Memory 候选 Payload 已脱敏")
        return self._record_from_row(row)

    async def ensure_exists_for_subject(
        self, candidate_id: str, *, identity: AgentIdentity
    ) -> None:
        """只校验候选归属，不解密正文，供脱敏后的审计查询使用。"""

        statement = text(
            """
            SELECT 1 FROM agent_memory_candidates
            WHERE id = :id AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "id": candidate_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise MemoryCandidateNotFound("未找到 Memory 候选")

    async def decide(
        self,
        candidate_id: str,
        *,
        identity: AgentIdentity,
        decision: Literal["APPROVED", "REJECTED"],
        decision_request_id: str,
        now: datetime,
    ) -> MemoryCandidateRecord:
        """在行锁事务中幂等记录用户决定。"""

        statement = text(
            """
            SELECT * FROM agent_memory_candidates
            WHERE id = :id AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            FOR UPDATE
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        update = text(
            """
            UPDATE agent_memory_candidates
            SET status = :status, decision_request_id = :decision_request_id,
                decided_at = :decided_at,
                retention_until = CURRENT_TIMESTAMP + (:retention_days * INTERVAL '1 day'),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'PENDING'
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "id": candidate_id,
                            "subject_user_id": identity.subject,
                            "organization_ids": list(identity.organization_ids),
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise MemoryCandidateNotFound("未找到 Memory 候选")
            if row["status"] == decision and row["decision_request_id"] == decision_request_id:
                return self._record_from_row(row)
            if row["status"] != "PENDING":
                raise MemoryCandidateStateError("Memory 候选决定已经是终态")
            updated = (
                (
                    await connection.execute(
                        update,
                        {
                            "id": candidate_id,
                            "status": decision,
                            "decision_request_id": decision_request_id,
                            "decided_at": now,
                            "retention_days": self._terminal_retention_days,
                        },
                    )
                )
                .mappings()
                .one()
            )
            await self._insert_event(
                connection,
                candidate_id=candidate_id,
                subject_user_id=identity.subject,
                organization_id=str(updated["organization_id"]),
                event_type=decision,
                actor_type="USER",
                actor_user_id=identity.subject,
                status_after=decision,
                request_id=decision_request_id,
                decision_request_id=decision_request_id,
                payload_hash=str(updated["payload_hash"]),
            )
        return self._record_from_row(updated)

    async def expire_due(self, *, limit: int = 500) -> int:
        """批量标记过期候选，供后续 Worker 或运维任务调用。"""

        statement = text(
            """
            WITH due AS (
                SELECT id FROM agent_memory_candidates
                WHERE status = 'PENDING' AND expires_at <= CURRENT_TIMESTAMP
                ORDER BY expires_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_memory_candidates AS candidate
            SET status = 'EXPIRED', decision_request_id = 'system:expiry',
                decided_at = CURRENT_TIMESTAMP,
                retention_until = CURRENT_TIMESTAMP + (:retention_days * INTERVAL '1 day'),
                updated_at = CURRENT_TIMESTAMP
            FROM due WHERE candidate.id = due.id
            RETURNING candidate.*
            """
        )
        async with self._database.engine.begin() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {"limit": limit, "retention_days": self._terminal_retention_days},
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                candidate_id = str(row["id"])
                await self._insert_event(
                    connection,
                    candidate_id=candidate_id,
                    subject_user_id=str(row["subject_user_id"]),
                    organization_id=str(row["organization_id"]),
                    event_type="EXPIRED",
                    actor_type="SYSTEM",
                    actor_user_id=None,
                    status_after="EXPIRED",
                    request_id=f"system:expiry:{candidate_id}",
                    decision_request_id="system:expiry",
                    payload_hash=str(row["payload_hash"]),
                )
        return len(rows)

    async def _insert_event(
        self,
        connection: Any,
        *,
        candidate_id: str,
        subject_user_id: str,
        organization_id: str,
        event_type: str,
        actor_type: str,
        actor_user_id: str | None,
        status_after: str,
        request_id: str,
        decision_request_id: str | None,
        payload_hash: str,
    ) -> None:
        """在候选状态事务内追加审计事件，避免出现状态已变但审计缺失。"""

        await connection.execute(
            text(
                """
                INSERT INTO agent_memory_candidate_events (
                    candidate_id, subject_user_id, organization_id, event_type,
                    actor_type, actor_user_id, status_after, request_id,
                    decision_request_id, payload_hash
                ) VALUES (
                    :candidate_id, :subject_user_id, :organization_id, :event_type,
                    :actor_type, :actor_user_id, :status_after, :request_id,
                    :decision_request_id, :payload_hash
                )
                """
            ),
            {
                "candidate_id": candidate_id,
                "subject_user_id": subject_user_id,
                "organization_id": organization_id,
                "event_type": event_type,
                "actor_type": actor_type,
                "actor_user_id": actor_user_id,
                "status_after": status_after,
                "request_id": request_id,
                "decision_request_id": decision_request_id,
                "payload_hash": payload_hash,
            },
        )

    def _record_from_row(self, row: Any) -> MemoryCandidateRecord:
        try:
            if str(row["payload_key_version"]) != self._cipher.key_version:
                raise ConfirmationPayloadCipherError(
                    "候选加密密钥版本不可用"
                )
            plaintext = self._cipher.decrypt(
                bytes(row["payload_ciphertext"]), associated_data=str(row["payload_hash"])
            )
            candidate = MemoryCandidate.model_validate(json.loads(plaintext))
        except (ConfirmationPayloadCipherError, json.JSONDecodeError, ValidationError) as exc:
            raise MemoryCandidateStateError("无法恢复 Memory 候选 Payload") from exc
        return MemoryCandidateRecord(
            id=str(row["id"]),
            subject_user_id=str(row["subject_user_id"]),
            organization_id=str(row["organization_id"]),
            candidate=candidate,
            payload_hash=str(row["payload_hash"]),
            source_thread_id=str(row["source_thread_id"]),
            source_request_id=str(row["source_request_id"]),
            status=row["status"],
            expires_at=_as_utc(row["expires_at"]),
            created_at=_as_utc(row["created_at"]),
            updated_at=_as_utc(row["updated_at"]),
            decision_request_id=row["decision_request_id"],
            decided_at=_as_utc(row["decided_at"]) if row["decided_at"] else None,
        )


def _sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def event_from_row(row: Any) -> MemoryCandidateEventRecord:
    """把审计事件映射成不包含 SQLAlchemy 细节的领域对象。"""

    return MemoryCandidateEventRecord(
        id=int(row["id"]),
        candidate_id=str(row["candidate_id"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        event_type=row["event_type"],
        actor_type=row["actor_type"],
        actor_user_id=row["actor_user_id"],
        status_after=row["status_after"],
        request_id=str(row["request_id"]),
        decision_request_id=row["decision_request_id"],
        payload_hash=str(row["payload_hash"]),
        created_at=_as_utc(row["created_at"]),
    )
