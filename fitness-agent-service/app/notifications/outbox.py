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

from sqlalchemy import bindparam, text


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


@dataclass(frozen=True)
class InAppNotificationRecord:
    """站内通知收件箱视图；标题和正文是发布时保存的模板渲染快照。"""

    id: str
    notification_type: str
    subject_user_id: str
    organization_id: str
    aggregate_type: str
    aggregate_id: str
    template_version: int
    title: str
    body: str
    status: str
    created_at: datetime
    read_at: datetime | None


@dataclass(frozen=True)
class NotificationDeliveryAttemptRecord:
    """管理员运维查询使用的投递尝试摘要，不包含通知正文和用户主体 ID。"""

    id: int
    outbox_id: str
    notification_type: str
    organization_id: str
    channel: str
    attempt_no: int
    status: str
    error_code: str | None
    provider_message_id: str | None
    started_at: datetime
    finished_at: datetime | None


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
                    status IN ('PENDING', 'DEFERRED', 'RETRYABLE_FAILED')
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

    async def mark_deferred(
        self,
        connection: Any,
        *,
        outbox_id: str,
        worker_id: str,
        available_at: datetime,
        reason: str,
    ) -> bool:
        """安静时间内不算失败，延迟到下一个允许时间再领取。"""

        if available_at <= datetime.now(UTC) or not reason.strip():
            raise ValueError("deferred notification must have a future time and reason")
        result = await connection.execute(
            text(
                """
                UPDATE agent_notification_outbox
                SET status = 'DEFERRED', available_at = :available_at,
                    last_error_code = NULL, locked_by = NULL, locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {
                "id": outbox_id,
                "worker_id": worker_id,
                "available_at": available_at,
            },
        )
        return int(result.rowcount or 0) == 1

    async def mark_suppressed(
        self,
        connection: Any,
        *,
        outbox_id: str,
        worker_id: str,
        reason: str,
    ) -> bool:
        """用户关闭或频率限制时终态抑制，不进入无限重试。"""

        if not reason.strip():
            raise ValueError("suppression reason is required")
        result = await connection.execute(
            text(
                """
                UPDATE agent_notification_outbox
                SET status = 'SUPPRESSED', suppressed_at = CURRENT_TIMESTAMP,
                    suppression_reason = :reason, locked_by = NULL, locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {"id": outbox_id, "worker_id": worker_id, "reason": reason[:128]},
        )
        return int(result.rowcount or 0) == 1

    async def write_in_app_notification(
        self,
        connection: Any,
        *,
        record: NotificationOutboxRecord,
        template_version: int,
        title: str,
        body: str,
    ) -> str:
        """幂等写入站内收件箱并返回收件箱 ID。

        收件箱使用 Outbox 的 ``dedupe_key`` 做唯一约束；适配器只负责渠道写入，Outbox
        状态和投递尝试状态由 Worker 在同一成功事务中确认。
        """

        if template_version < 1 or not title.strip() or not body.strip():
            raise ValueError("notification delivery snapshot is invalid")
        inserted = (
            await connection.execute(
                text(
                    """
                        INSERT INTO agent_in_app_notifications (
                            id, notification_type, subject_user_id, organization_id,
                            aggregate_type, aggregate_id, dedupe_key,
                            template_version, title, body
                        ) VALUES (
                            :id, :notification_type, :subject_user_id, :organization_id,
                            :aggregate_type, :aggregate_id, :dedupe_key,
                            :template_version, :title, :body
                        )
                        ON CONFLICT (dedupe_key) DO NOTHING
                        RETURNING id
                        """
                ),
                {
                    "id": _new_id(),
                    "notification_type": record.notification_type,
                    "subject_user_id": record.subject_user_id,
                    "organization_id": record.organization_id,
                    "aggregate_type": record.aggregate_type,
                    "aggregate_id": record.aggregate_id,
                    "dedupe_key": record.dedupe_key,
                    "template_version": template_version,
                    "title": title,
                    "body": body,
                },
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return str(inserted)
        existing = (
            await connection.execute(
                text("SELECT id FROM agent_in_app_notifications WHERE dedupe_key = :dedupe_key"),
                {"dedupe_key": record.dedupe_key},
            )
        ).scalar_one_or_none()
        if existing is None:
            raise RuntimeError("in-app notification dedupe row disappeared")
        return str(existing)

    async def start_delivery_attempt(
        self,
        connection: Any,
        *,
        outbox_id: str,
        channel: str,
        attempt_no: int,
    ) -> None:
        """记录投递开始；相同 Outbox、渠道和次数只创建一条尝试。"""

        if not outbox_id.strip() or not channel.strip() or attempt_no < 1:
            raise ValueError("delivery attempt parameters are invalid")
        await connection.execute(
            text(
                """
                INSERT INTO agent_notification_delivery_attempts (
                    outbox_id, channel, attempt_no, status
                ) VALUES (
                    :outbox_id, :channel, :attempt_no, 'STARTED'
                )
                ON CONFLICT (outbox_id, channel, attempt_no) DO NOTHING
                """
            ),
            {
                "outbox_id": outbox_id,
                "channel": channel,
                "attempt_no": attempt_no,
            },
        )

    async def finish_delivery_attempt(
        self,
        connection: Any,
        *,
        outbox_id: str,
        channel: str,
        attempt_no: int,
        status: str,
        error_code: str | None = None,
        provider_message_id: str | None = None,
    ) -> bool:
        """完成一次投递尝试，状态只允许进入终态。"""

        if status not in {"SUCCEEDED", "RETRYABLE_FAILED", "FINAL_FAILED"}:
            raise ValueError("delivery attempt status is invalid")
        result = await connection.execute(
            text(
                """
                UPDATE agent_notification_delivery_attempts
                SET status = :status, error_code = :error_code,
                    provider_message_id = :provider_message_id,
                    finished_at = CURRENT_TIMESTAMP
                WHERE outbox_id = :outbox_id AND channel = :channel
                  AND attempt_no = :attempt_no AND status = 'STARTED'
                """
            ),
            {
                "outbox_id": outbox_id,
                "channel": channel,
                "attempt_no": attempt_no,
                "status": status,
                "error_code": error_code[:128] if error_code else None,
                "provider_message_id": provider_message_id,
            },
        )
        return int(result.rowcount or 0) == 1

    async def list_delivery_attempts(
        self,
        connection: Any,
        *,
        organization_id: str | None = None,
        notification_type: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[NotificationDeliveryAttemptRecord]:
        """查询投递尝试摘要，供管理员定位失败、重试和最终失败。

        这是运维视图，不返回标题、正文、subject_user_id 或 aggregate_id。即使管理员
        需要排查失败，也只应该看到通知类型、机构范围和错误码，避免把个人健身偏好
        通过运维接口再次扩大暴露面。
        """

        if limit < 1 or limit > 100:
            raise ValueError("delivery attempt list limit must be between 1 and 100")
        if organization_id is not None and not organization_id.strip():
            raise ValueError("organization id cannot be blank")
        if notification_type is not None and not notification_type.strip():
            raise ValueError("notification type cannot be blank")
        if channel is not None and not channel.strip():
            raise ValueError("notification channel cannot be blank")
        if status is not None and status not in {
            "STARTED",
            "SUCCEEDED",
            "RETRYABLE_FAILED",
            "FINAL_FAILED",
        }:
            raise ValueError("delivery attempt status is invalid")

        rows = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT attempt.id, attempt.outbox_id, outbox.notification_type,
                               outbox.organization_id, attempt.channel, attempt.attempt_no,
                               attempt.status, attempt.error_code,
                               attempt.provider_message_id, attempt.started_at,
                               attempt.finished_at
                        FROM agent_notification_delivery_attempts AS attempt
                        JOIN agent_notification_outbox AS outbox
                          ON outbox.id = attempt.outbox_id
                        WHERE (CAST(:organization_id AS TEXT) IS NULL
                               OR outbox.organization_id = CAST(:organization_id AS TEXT))
                          AND (CAST(:notification_type AS TEXT) IS NULL
                               OR outbox.notification_type = CAST(:notification_type AS TEXT))
                          AND (CAST(:channel AS TEXT) IS NULL
                               OR attempt.channel = CAST(:channel AS TEXT))
                          AND (CAST(:status AS TEXT) IS NULL
                               OR attempt.status = CAST(:status AS TEXT))
                        ORDER BY attempt.started_at DESC, attempt.id DESC
                        LIMIT :limit
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "notification_type": notification_type,
                        "channel": channel,
                        "status": status,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_delivery_attempt_from_row(row) for row in rows]

    async def list_in_app(
        self,
        connection: Any,
        *,
        subject_user_id: str,
        organization_id: str,
        status: str | None,
        limit: int,
    ) -> list[InAppNotificationRecord]:
        """按签名主体和机构读取站内通知，不允许调用方传入任意用户 ID。"""

        if limit < 1 or limit > 100:
            raise ValueError("notification list limit must be between 1 and 100")
        statement = text(
            """
            SELECT * FROM agent_in_app_notifications
            WHERE subject_user_id = :subject_user_id
              AND organization_id = :organization_id
              AND (CAST(:status AS TEXT) IS NULL OR status = CAST(:status AS TEXT))
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        )
        rows = (
            (
                await connection.execute(
                    statement,
                    {
                        "subject_user_id": subject_user_id,
                        "organization_id": organization_id,
                        "status": status,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_in_app_from_row(row) for row in rows]

    async def mark_in_app_read(
        self,
        connection: Any,
        *,
        notification_id: str,
        subject_user_id: str,
        organization_ids: list[str],
    ) -> InAppNotificationRecord | None:
        """标记本人通知已读；重复标记是幂等的。"""

        row = (
            (
                await connection.execute(
                    text(
                        """
                        UPDATE agent_in_app_notifications
                        SET status = 'READ', read_at = COALESCE(read_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                          AND subject_user_id = :subject_user_id
                          AND organization_id IN :organization_ids
                        RETURNING *
                        """
                    ).bindparams(bindparam("organization_ids", expanding=True)),
                    {
                        "id": notification_id,
                        "subject_user_id": subject_user_id,
                        "organization_ids": organization_ids,
                    },
                )
            )
            .mappings()
            .first()
        )
        return _in_app_from_row(row) if row is not None else None


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


def _delivery_attempt_from_row(row: Any) -> NotificationDeliveryAttemptRecord:
    """把数据库行转换成不携带正文的运维摘要。"""

    started_at = row["started_at"]
    if started_at is None:
        raise RuntimeError("notification delivery attempt started_at is NULL")
    return NotificationDeliveryAttemptRecord(
        id=int(row["id"]),
        outbox_id=str(row["outbox_id"]),
        notification_type=str(row["notification_type"]),
        organization_id=str(row["organization_id"]),
        channel=str(row["channel"]),
        attempt_no=int(row["attempt_no"]),
        status=str(row["status"]),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        provider_message_id=(
            str(row["provider_message_id"]) if row["provider_message_id"] is not None else None
        ),
        started_at=started_at,
        finished_at=row["finished_at"],
    )


def _in_app_from_row(row: Any) -> InAppNotificationRecord:
    """把站内通知行映射为安全的用户视图。"""

    return InAppNotificationRecord(
        id=str(row["id"]),
        notification_type=str(row["notification_type"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        template_version=int(row["template_version"]),
        title=str(row["title"]),
        body=str(row["body"]),
        status=str(row["status"]),
        created_at=_required_utc(row["created_at"]),
        read_at=_as_utc(row["read_at"]),
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
