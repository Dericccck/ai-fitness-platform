"""主动提醒事件 Inbox 的 PostgreSQL 持久化。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text

from .events import ProactiveEventMessage


@dataclass(frozen=True)
class ProactiveEventRecord:
    """已进入 Agent Inbox 的事件及其处理租约。"""

    event_id: str
    source: str
    event_type: str
    aggregate_id: str
    organization_id: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    available_at: datetime
    locked_by: str | None
    locked_at: datetime | None
    contract_version: int = 1
    aggregate_version: int | None = None
    occurred_at: datetime | None = None


class ProactiveEventRepository:
    """提供事件去重、领取、成功和有限重试接口。"""

    async def accept(
        self,
        connection: Any,
        *,
        event: ProactiveEventMessage,
    ) -> bool:
        """幂等接收事件；重复 event_id 直接返回 False，RabbitMQ 可安全确认消息。"""

        result = await connection.execute(
            text(
                """
                INSERT INTO agent_proactive_event_inbox (
                    event_id, source, event_type, aggregate_id, organization_id, payload,
                    contract_version, aggregate_version, occurred_at
                ) VALUES (
                    :event_id, :source, :event_type, :aggregate_id, :organization_id,
                    CAST(:payload AS JSONB), :contract_version, :aggregate_version, :occurred_at
                )
                ON CONFLICT (event_id) DO NOTHING
                """
            ),
            {
                "event_id": event.event_id,
                "source": event.source,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "organization_id": event.organization_id,
            "payload": json.dumps(event.payload, ensure_ascii=False),
            "contract_version": event.contract_version,
            "aggregate_version": event.aggregate_version,
            "occurred_at": event.occurred_at,
            },
        )
        return int(result.rowcount or 0) == 1

    async def claim_batch(
        self,
        connection: Any,
        *,
        worker_id: str,
        limit: int,
        lock_timeout_seconds: int = 300,
    ) -> list[ProactiveEventRecord]:
        """原子领取事件，支持多实例并行且可恢复失联 Worker 的租约。"""

        if not worker_id.strip() or limit < 1 or limit > 500:
            raise ValueError("必须提供主动事件 Worker ID 和安全批次限制")
        rows = (
            (
                await connection.execute(
                    text(
                        """
                        WITH picked AS (
                            SELECT event_id
                            FROM agent_proactive_event_inbox
                            WHERE (
                                status IN ('PENDING', 'RETRYABLE_FAILED')
                                AND available_at <= CURRENT_TIMESTAMP
                            ) OR (
                                status = 'PROCESSING'
                                AND locked_at <= CURRENT_TIMESTAMP - (:lock_timeout * INTERVAL '1 second')
                            )
                            ORDER BY created_at, event_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT :limit
                        )
                        UPDATE agent_proactive_event_inbox AS inbox
                        SET status = 'PROCESSING', locked_by = :worker_id,
                            locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                        FROM picked
                        WHERE inbox.event_id = picked.event_id
                        RETURNING inbox.*
                        """
                    ),
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

    async def mark_processed(self, connection: Any, *, event_id: str, worker_id: str) -> bool:
        """通知 Outbox 和事件状态在同一事务完成后，才把事件标记为已处理。"""

        result = await connection.execute(
            text(
                """
                UPDATE agent_proactive_event_inbox
                SET status = 'PROCESSED', processed_at = CURRENT_TIMESTAMP,
                    locked_by = NULL, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE event_id = :event_id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {"event_id": event_id, "worker_id": worker_id},
        )
        return int(result.rowcount or 0) == 1

    async def has_newer_superseding_event(
        self,
        connection: Any,
        *,
        organization_id: str,
        aggregate_id: str,
        aggregate_version: int | None,
        superseding_types: tuple[str, ...],
    ) -> bool:
        """判断当前待办是否已被更高业务版本取代；无版本事件保持历史兼容。"""

        if aggregate_version is None or not superseding_types:
            return False
        count = await connection.scalar(
            text(
                """
                SELECT COUNT(1)
                FROM agent_proactive_event_inbox
                WHERE organization_id = :organization_id
                  AND aggregate_id = :aggregate_id
                  AND aggregate_version > :aggregate_version
                  AND event_type IN :superseding_types
                """
            ).bindparams(bindparam("superseding_types", expanding=True)),
            {
                "organization_id": organization_id,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "superseding_types": superseding_types,
            },
        )
        return int(count or 0) > 0

    async def mark_retry(
        self,
        connection: Any,
        *,
        event_id: str,
        worker_id: str,
        error_code: str,
        delay_seconds: int = 30,
        max_attempts: int = 8,
    ) -> bool:
        """失败事件有限重试，超过上限进入 DEAD，等待人工补偿。"""

        if not error_code.strip() or delay_seconds < 1 or max_attempts < 1:
            raise ValueError("主动事件重试参数无效")
        result = await connection.execute(
            text(
                """
                UPDATE agent_proactive_event_inbox
                SET attempt_count = attempt_count + 1,
                    status = CASE WHEN attempt_count + 1 >= :max_attempts
                                  THEN 'DEAD' ELSE 'RETRYABLE_FAILED' END,
                    available_at = CURRENT_TIMESTAMP + (:delay * INTERVAL '1 second'),
                    last_error_code = :error_code, locked_by = NULL, locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE event_id = :event_id AND status = 'PROCESSING' AND locked_by = :worker_id
                """
            ),
            {
                "event_id": event_id,
                "worker_id": worker_id,
                "error_code": error_code[:128],
                "delay": delay_seconds,
                "max_attempts": max_attempts,
            },
        )
        return int(result.rowcount or 0) == 1


def _from_row(row: Any) -> ProactiveEventRecord:
    return ProactiveEventRecord(
        event_id=str(row["event_id"]),
        source=str(row["source"]),
        event_type=str(row["event_type"]),
        aggregate_id=str(row["aggregate_id"]),
        organization_id=str(row["organization_id"]),
        payload=dict(row["payload"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        available_at=_required_utc(row["available_at"]),
        locked_by=str(row["locked_by"]) if row["locked_by"] else None,
        locked_at=_as_utc(row["locked_at"]),
        contract_version=int(row.get("contract_version") or 1),
        aggregate_version=int(row["aggregate_version"]) if row.get("aggregate_version") is not None else None,
        occurred_at=_as_utc(row.get("occurred_at")),
    )


def _required_utc(value: Any) -> datetime:
    result = _as_utc(value)
    if result is None:
        raise RuntimeError("主动事件必需的时间戳为 NULL")
    return result


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("主动事件时间戳类型无效")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def new_worker_id() -> str:
    return f"proactive-worker:{uuid4()}"
