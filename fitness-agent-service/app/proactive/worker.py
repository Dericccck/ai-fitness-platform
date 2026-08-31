"""主动提醒事件 Worker：Inbox 事件转换为通知 Outbox。"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository

from .events import ProactiveEventContractError, ProactiveEventMessage, notification_targets
from .repository import ProactiveEventRecord, ProactiveEventRepository, new_worker_id

_logger = structlog.get_logger("proactive.event_worker")
_WORKER_NAME = "proactive_event"


@dataclass(frozen=True)
class ProactiveEventRunResult:
    """一次主动提醒事件处理轮询的结果。"""

    claimed: int
    processed: int
    retried: int


class ProactiveEventWorker:
    """把已经可靠落库的业务事件转换为站内通知 Outbox。

    RabbitMQ 只承担跨服务传输，PostgreSQL Inbox 才承担消费幂等和重试状态。这样即使
    RabbitMQ 消费进程重启，事件也不会因为“已确认消息但尚未生成通知”而丢失；通知 Outbox
    的唯一 dedupe_key 再保证同一个事件对同一个用户最多生成一条通知。
    """

    def __init__(
        self,
        database: Database,
        event_repository: ProactiveEventRepository,
        notification_repository: NotificationOutboxRepository,
        *,
        batch_size: int = 50,
        worker_id: str | None = None,
        metrics: HttpMetrics | None = None,
        max_attempts: int = 8,
    ) -> None:
        if batch_size < 1 or batch_size > 500:
            raise ValueError("主动事件批次大小必须在 1 到 500 之间")
        if max_attempts < 1 or max_attempts > 20:
            raise ValueError("主动事件最大尝试次数必须在 1 到 20 之间")
        self.database = database
        self.event_repository = event_repository
        self.notification_repository = notification_repository
        self.batch_size = batch_size
        self.worker_id = worker_id or new_worker_id()
        self.metrics = metrics
        self.max_attempts = max_attempts

    async def run_once(self) -> ProactiveEventRunResult:
        """有界领取并处理一批事件；单条失败不阻塞同批其他事件。"""

        async with self.database.engine.begin() as connection:
            records = await self.event_repository.claim_batch(
                connection,
                worker_id=self.worker_id,
                limit=self.batch_size,
            )
        processed = 0
        retried = 0
        for record in records:
            try:
                await self._process_one(record)
                processed += 1
            except Exception:
                retried += 1
                _logger.exception(
                    "proactive_event_processing_failed",
                    event_id=record.event_id,
                    event_type=record.event_type,
                )
                async with self.database.engine.begin() as connection:
                    if not await self.event_repository.mark_retry(
                        connection,
                        event_id=record.event_id,
                        worker_id=self.worker_id,
                        error_code="PROACTIVE_EVENT_PROCESSING_FAILED",
                        max_attempts=self.max_attempts,
                    ):
                        raise RuntimeError("重试主动事件时丢失了主动事件锁")
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="processed"
            ).inc(processed)
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="retryable_failed"
            ).inc(retried)
        return ProactiveEventRunResult(claimed=len(records), processed=processed, retried=retried)

    async def _process_one(self, record: ProactiveEventRecord) -> None:
        event = _event_from_record(record)
        targets = notification_targets(event)
        async with self.database.engine.begin() as connection:
            for target in targets:
                await self.notification_repository.enqueue_on_connection(
                    connection,
                    notification_type=event.event_type,
                    subject_user_id=target.user_id,
                    organization_id=event.organization_id,
                    aggregate_type="APPOINTMENT"
                    if event.event_type.startswith("APPOINTMENT_")
                    else "TRAINING_PLAN",
                    aggregate_id=event.aggregate_id,
                    dedupe_key=f"proactive:{event.event_id}:{target.user_id}",
                    payload={
                        "event_id": event.event_id,
                        "recipient_role": target.role,
                    },
                )
            processed = await self.event_repository.mark_processed(
                connection,
                event_id=record.event_id,
                worker_id=self.worker_id,
            )
            if not processed:
                        raise RuntimeError("处理主动事件时丢失了主动事件锁")


def _event_from_record(record: ProactiveEventRecord) -> ProactiveEventMessage:
    try:
        return ProactiveEventMessage.model_validate(
            {
                "event_id": record.event_id,
                "source": record.source,
                "event_type": record.event_type,
                "aggregate_id": record.aggregate_id,
                "organization_id": record.organization_id,
                "payload": record.payload,
            }
        )
    except Exception as exc:
        raise ProactiveEventContractError("存储的主动事件无效") from exc
