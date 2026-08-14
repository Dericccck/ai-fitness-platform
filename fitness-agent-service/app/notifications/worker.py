"""站内通知 Outbox 发布 Worker。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database

from .outbox import NotificationOutboxRecord, NotificationOutboxRepository

_logger = structlog.get_logger("notifications.outbox_worker")
_WORKER_NAME = "notification_outbox"


@dataclass(frozen=True)
class NotificationOutboxRunResult:
    """一次有界发布轮询的结果。"""

    claimed: int
    published: int
    retried: int


class NotificationOutboxWorker:
    """把 Outbox 事件发布到站内通知收件箱。

    Worker 不直接发送短信或 Push；站内通知是当前项目的第一种可用渠道。未来接入
    RabbitMQ 时，可以保留同一套领取、幂等和失败状态，把 ``publish_to_inbox`` 替换为
    消息发布适配器，候选业务和 Outbox 表无需改动。
    """

    def __init__(
        self,
        database: Database,
        repository: NotificationOutboxRepository,
        *,
        batch_size: int = 100,
        worker_id: str | None = None,
        metrics: HttpMetrics | None = None,
    ) -> None:
        if batch_size < 1 or batch_size > 500:
            raise ValueError("notification outbox batch size must be between 1 and 500")
        self.database = database
        self.repository = repository
        self.batch_size = batch_size
        self.worker_id = worker_id or f"notification-worker:{uuid4()}"
        self.metrics = metrics

    async def run_once(self) -> NotificationOutboxRunResult:
        """领取一批事件，逐条写入站内收件箱，失败时进入有限重试。"""

        try:
            async with self.database.engine.begin() as connection:
                records = await self.repository.claim_batch(
                    connection,
                    worker_id=self.worker_id,
                    limit=self.batch_size,
                )
            published = 0
            retried = 0
            for record in records:
                if await self._publish_one(record):
                    published += 1
                else:
                    retried += 1
        except Exception:
            if self.metrics is not None:
                self.metrics.maintenance_runs_total.labels(
                    worker=_WORKER_NAME, status="failed"
                ).inc()
            _logger.exception("notification_outbox_batch_failed")
            raise

        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="published"
            ).inc(published)
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="retryable_failed"
            ).inc(retried)
        _logger.info(
            "notification_outbox_batch",
            claimed_count=len(records),
            published_count=published,
            retried_count=retried,
            batch_size=self.batch_size,
        )
        return NotificationOutboxRunResult(
            claimed=len(records), published=published, retried=retried
        )

    async def _publish_one(self, record: NotificationOutboxRecord) -> bool:
        try:
            async with self.database.engine.begin() as connection:
                published = await self.repository.publish_to_inbox(
                    connection,
                    record=record,
                    worker_id=self.worker_id,
                )
                if not published:
                    raise RuntimeError("notification outbox lock was lost")
            return True
        except Exception:
            _logger.exception(
                "notification_outbox_publish_failed",
                outbox_id=record.id,
                notification_type=record.notification_type,
            )
            async with self.database.engine.begin() as connection:
                await self.repository.mark_retry(
                    connection,
                    outbox_id=record.id,
                    worker_id=self.worker_id,
                    error_code="IN_APP_PUBLISH_FAILED",
                )
            return False
