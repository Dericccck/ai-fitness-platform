"""通知 Outbox 的 PostgreSQL 持久化。

当前阶段只负责可靠地产生待发布通知事件，不假装已经接入短信、Push 或站内信供应商。
下游消息发布器可以使用 claim/mark_published/mark_retry 这组接口接入 RabbitMQ 或
其他企业消息系统；候选创建事务与 Outbox 写入事务保持一致，避免“候选已生成但通知任务
丢失”或“通知已生成但候选回滚”的双写不一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


@dataclass(frozen=True)
class NotificationOutboxRecord:
    """一条不包含通知正文的待发布事件。"""

    id: str
    notification_type: str
    subject_user_id: str
    organization_id: str
    aggregate_type: str
    aggregate_id: str
    dedupe_key: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    available_at: datetime
    locked_by: str | None
    locked_at: datetime | None
    last_error_code: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationOutboxRepository:
    """提供事务内入队和下游发布器需要的抢占/确认接口。"""

    @staticmethod
    async def enqueue_on_connection(
        connection: Any,
        *,
        notification_type: str,
        subject_user_id: str,
        organization_id: str,
        aggregate_type: str,
        aggregate_id: str,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> bool:
        """在调用方事务中入队；重复幂等键返回 False，不产生重复通知。"""

        result = await connection.execute(
            text(
                """
                INSERT INTO agent_notification_outbox (
                    id, notification_type, subject_user_id, organization_id,
                    aggregate_type, aggregate_id, dedupe_key, payload
                ) VALUES (
                    :id, :notification_type, :subject_user_id, :organization_id,
                    :aggregate_type, :aggregate_id, :dedupe_key, CAST(:payload AS JSONB)
                )
                ON CONFLICT (dedupe_key) DO NOTHING
                """
            ),
            {
                "id": _new_id(),
                "notification_type": notification_type,
                "subject_user_id": subject_user_id,
                "organization_id": organization_id,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "dedupe_key": dedupe_key,
                # payload 只放候选 ID 和事件类型，通知正文由受信任下游按权限读取，
                # 避免把加密候选解密后再次写入通用消息表。
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        return int(result.rowcount or 0) == 1

    async def claim_batch(
        self,
        connection: Any,
        *,
        worker_id: str,
        limit: int = 100,
        lock_timeout_seconds: int = 300,
    ) -> list[NotificationOutboxRecord]:
        """原子领取一批待发布事件，支持多个发布器实例并行消费。"""

        if not worker_id.strip() or limit < 1 or limit > 500:
            raise ValueError("notification worker id and safe batch limit are required")
        statement = text(
            """
            WITH picked AS (
                SELECT id
                FROM agent_notification_outbox
                WHERE (
                    status IN ('PENDING', 'RETRYABLE_FAILED')
                    AND available_at <= CURRENT_TIMESTAMP
                ) OR (
                    status = 'PROCESSING'
                    AND locked_at <= CURRENT_TIMESTAMP - (:lock_timeout * INTERVAL '1 second')
                )
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_notification_outbox AS outbox
            SET status = 'PROCESSING', locked_by = :worker_id,
                locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            FROM picked
            WHERE outbox.id = picked.id
            RETURNING outbox.*
            """
        )
        rows = (
            (
                await connection.execute(
                    statement,
                    {
                        "worker_id": worker_id,
                        "limit": limit,
                        "lock_timeout": lock_timeout_seconds,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_from_row(row) for row in rows]

    async def mark_published(self, connection: Any, *, outbox_id: str, worker_id: str) -> bool:
        """发布器成功交给消息系统后确认事件。"""

        result = await connection.execute(
            text(
                """
                UPDATE agent_notification_outbox
                SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP,
                    locked_by = NULL, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {"id": outbox_id, "worker_id": worker_id},
        )
        return int(result.rowcount or 0) == 1

    async def mark_retry(
        self,
        connection: Any,
        *,
        outbox_id: str,
        worker_id: str,
        error_code: str,
        delay_seconds: int = 60,
        max_attempts: int = 8,
    ) -> bool:
        """发布失败时进入重试或死信状态，不允许无限重试。"""

        if not error_code.strip() or delay_seconds < 1 or max_attempts < 1:
            raise ValueError("notification retry parameters are invalid")
        result = await connection.execute(
            text(
                """
                UPDATE agent_notification_outbox
                SET attempt_count = attempt_count + 1,
                    status = CASE WHEN attempt_count + 1 >= :max_attempts
                                  THEN 'DEAD' ELSE 'RETRYABLE_FAILED' END,
                    available_at = CURRENT_TIMESTAMP + (:delay * INTERVAL '1 second'),
                    last_error_code = :error_code, locked_by = NULL, locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {
                "id": outbox_id,
                "worker_id": worker_id,
                "error_code": error_code[:128],
                "delay": delay_seconds,
                "max_attempts": max_attempts,
            },
        )
        return int(result.rowcount or 0) == 1


def _from_row(row: Any) -> NotificationOutboxRecord:
    """把数据库行映射成下游发布器使用的稳定对象。"""

    return NotificationOutboxRecord(
        id=str(row["id"]),
        notification_type=str(row["notification_type"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        dedupe_key=str(row["dedupe_key"]),
        payload=dict(row["payload"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        available_at=_required_utc(row["available_at"]),
        locked_by=str(row["locked_by"]) if row["locked_by"] else None,
        locked_at=_as_utc(row["locked_at"]),
        last_error_code=str(row["last_error_code"]) if row["last_error_code"] else None,
        published_at=_as_utc(row["published_at"]),
        created_at=_required_utc(row["created_at"]),
        updated_at=_required_utc(row["updated_at"]),
    )


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_utc(value: datetime) -> datetime:
    normalized = _as_utc(value)
    if normalized is None:
        raise RuntimeError("notification outbox required timestamp is NULL")
    return normalized
