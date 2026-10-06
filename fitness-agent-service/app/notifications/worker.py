"""站内通知 Outbox 发布 Worker。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import structlog
from sqlalchemy import text

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database
from app.proactive.candidate_repository import ProactiveCandidateRepository

from .channels import (
    InAppNotificationChannelAdapter,
    NotificationChannelAdapter,
    NotificationDeliveryRequest,
)
from .outbox import NotificationOutboxRecord, NotificationOutboxRepository
from .preferences import NotificationPreferenceRepository
from .templates import NotificationTemplateRepository, render_notification_template

_logger = structlog.get_logger("notifications.outbox_worker")
_WORKER_NAME = "notification_outbox"


@dataclass(frozen=True)
class NotificationOutboxRunResult:
    """一次有界发布轮询的结果。"""

    claimed: int
    published: int
    retried: int
    deferred: int
    suppressed: int


class NotificationOutboxWorker:
    """领取 Outbox 并通过渠道适配器投递通知。

    Worker 只编排策略、模板和投递状态，不依赖短信、Push 或 RabbitMQ SDK。当前默认
    注册 IN_APP 适配器；未来新增渠道时只增加适配器和配置，不修改候选业务。
    """

    def __init__(
        self,
        database: Database,
        repository: NotificationOutboxRepository,
        *,
        batch_size: int = 100,
        worker_id: str | None = None,
        metrics: HttpMetrics | None = None,
        preferences: NotificationPreferenceRepository | None = None,
        channel_adapters: dict[str, NotificationChannelAdapter] | None = None,
        max_delivery_attempts: int = 8,
        proactive_candidates: ProactiveCandidateRepository | None = None,
    ) -> None:
        if batch_size < 1 or batch_size > 500:
            raise ValueError("通知 Outbox 批次大小必须在 1 到 500 之间")
        if max_delivery_attempts < 1 or max_delivery_attempts > 20:
            raise ValueError("通知最大投递次数必须在 1 到 20 之间")
        self.database = database
        self.repository = repository
        self.batch_size = batch_size
        self.worker_id = worker_id or f"notification-worker:{uuid4()}"
        self.metrics = metrics
        self.preferences = preferences or NotificationPreferenceRepository()
        self.templates = NotificationTemplateRepository()
        self.max_delivery_attempts = max_delivery_attempts
        self.channel_adapters = channel_adapters or {
            "IN_APP": InAppNotificationChannelAdapter(repository)
        }
        self.proactive_candidates = proactive_candidates

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
            deferred = 0
            suppressed = 0
            for record in records:
                outcome = await self._publish_one(record)
                if outcome == "published":
                    published += 1
                elif outcome == "retried":
                    retried += 1
                elif outcome == "deferred":
                    deferred += 1
                else:
                    suppressed += 1
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
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="deferred"
            ).inc(deferred)
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="suppressed"
            ).inc(suppressed)
        _logger.info(
            "notification_outbox_batch",
            claimed_count=len(records),
            published_count=published,
            retried_count=retried,
            deferred_count=deferred,
            suppressed_count=suppressed,
            batch_size=self.batch_size,
        )
        return NotificationOutboxRunResult(
            claimed=len(records),
            published=published,
            retried=retried,
            deferred=deferred,
            suppressed=suppressed,
        )

    async def _publish_one(self, record: NotificationOutboxRecord) -> str:
        channel: Literal["IN_APP"] = "IN_APP"
        attempt_no = record.attempt_count + 1
        attempt_started = False
        try:
            adapter = self.channel_adapters.get(channel)
            if adapter is None:
                raise RuntimeError(f"不支持的通知渠道：{channel}")

            # 同一主体和通知类型使用事务级 advisory lock，把限频判断、站内信写入和
            # Outbox 完成串行化。这样两个 Worker 不会同时通过频率限制，也不会在用户
            # 刚关闭通知后仍跨事务继续投递。当前 IN_APP 渠道完全位于此事务内。
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_advisory_xact_lock(hashtext(:subject), hashtext(:notification_type))"
                    ),
                    {
                        "subject": record.subject_user_id,
                        "notification_type": record.notification_type,
                    },
                )
                if await self.repository.is_stale_before_delivery(connection, record=record):
                    suppressed = await self.repository.mark_suppressed(
                        connection,
                        outbox_id=record.id,
                        worker_id=self.worker_id,
                        reason="STALE_BEFORE_DELIVERY",
                    )
                    if not suppressed:
                        raise RuntimeError("抑制过期通知时丢失了通知 Outbox 锁")
                    await self._finish_candidate(
                        connection,
                        record=record,
                        status="IGNORED",
                        error_code="STALE_BEFORE_DELIVERY",
                    )
                    return "suppressed"
                decision = await self.preferences.evaluate(connection, record=record)
                if decision.action == "DEFER":
                    if decision.available_at is None:
                        raise RuntimeError("延迟通知没有下一次可用时间")
                    deferred = await self.repository.mark_deferred(
                        connection,
                        outbox_id=record.id,
                        worker_id=self.worker_id,
                        available_at=decision.available_at,
                        reason=decision.reason or "QUIET_HOURS",
                    )
                    if not deferred:
                        raise RuntimeError("延迟通知时丢失了通知 Outbox 锁")
                    return "deferred"
                if decision.action == "SUPPRESS":
                    suppressed = await self.repository.mark_suppressed(
                        connection,
                        outbox_id=record.id,
                        worker_id=self.worker_id,
                        reason=decision.reason or "POLICY_SUPPRESSED",
                    )
                    if not suppressed:
                        raise RuntimeError("抑制通知时丢失了通知 Outbox 锁")
                    await self._finish_candidate(
                        connection,
                        record=record,
                        status="IGNORED",
                        error_code=decision.reason or "POLICY_SUPPRESSED",
                    )
                    return "suppressed"
                await self.repository.start_delivery_attempt(
                    connection,
                    outbox_id=record.id,
                    channel=channel,
                    attempt_no=attempt_no,
                )
                attempt_started = True
                snapshot = None
                snapshot_id = record.payload.get("message_snapshot_id")
                if isinstance(snapshot_id, str) and self.proactive_candidates is not None:
                    snapshot = await self.proactive_candidates.get_message_snapshot(
                        connection, snapshot_id=snapshot_id
                    )
                    if snapshot is None:
                        raise RuntimeError("主动消息快照不存在")
                    title, body = snapshot.title, snapshot.body
                    template_version = snapshot.template_version
                else:
                    template = await self.templates.get_published(
                        connection,
                        template_key=record.notification_type,
                        channel=channel,
                    )
                    safe_values = {
                        key: value
                        for key, value in {
                            "aggregate_id": record.aggregate_id,
                            "notification_type": record.notification_type,
                        }.items()
                        if key in template.variables
                    }
                    title, body = render_notification_template(template, values=safe_values)
                    template_version = template.version
                receipt = await adapter.deliver(
                    connection,
                    NotificationDeliveryRequest(
                        record=record,
                        template_version=template_version,
                        title=title,
                        body=body,
                        proactive_candidate_id=snapshot.candidate_id if snapshot else None,
                        cta_type=snapshot.cta_type if snapshot else None,
                        cta_payload=snapshot.cta_payload if snapshot else None,
                    ),
                )
                finished = await self.repository.finish_delivery_attempt(
                    connection,
                    outbox_id=record.id,
                    channel=channel,
                    attempt_no=attempt_no,
                    status="SUCCEEDED",
                    provider_message_id=receipt.provider_message_id,
                )
                if not finished:
                    raise RuntimeError("通知投递尝试未处于开放状态")
                published = await self.repository.mark_published(
                    connection, outbox_id=record.id, worker_id=self.worker_id
                )
                if not published:
                    raise RuntimeError("发布通知时丢失了通知 Outbox 锁")
                await self._finish_candidate(connection, record=record, status="COMPLETED")
            # 指标在事务提交后再增加，避免数据库回滚但 Prometheus 已记录成功的假象。
            if self.metrics is not None:
                self.metrics.record_notification_delivery_attempt(channel, "SUCCEEDED")
            return "published"
        except Exception:
            _logger.exception(
                "notification_outbox_publish_failed",
                outbox_id=record.id,
                notification_type=record.notification_type,
            )
            failed_attempt_status: str | None = None
            async with self.database.engine.begin() as connection:
                if attempt_started:
                    # 投递事务失败时 STARTED 行会一并回滚；在补偿事务中幂等重建，
                    # 确保每次失败都留下可查询的渠道尝试记录。
                    await self.repository.start_delivery_attempt(
                        connection,
                        outbox_id=record.id,
                        channel=channel,
                        attempt_no=attempt_no,
                    )
                    attempt_status = (
                        "FINAL_FAILED"
                        if attempt_no >= self.max_delivery_attempts
                        else "RETRYABLE_FAILED"
                    )
                    finished = await self.repository.finish_delivery_attempt(
                        connection,
                        outbox_id=record.id,
                        channel=channel,
                        attempt_no=attempt_no,
                        status=attempt_status,
                        error_code="NOTIFICATION_DELIVERY_FAILED",
                    )
                    if finished and self.metrics is not None:
                        failed_attempt_status = attempt_status
                retried = await self.repository.mark_retry(
                    connection,
                    outbox_id=record.id,
                    worker_id=self.worker_id,
                    error_code="NOTIFICATION_DELIVERY_FAILED",
                    max_attempts=self.max_delivery_attempts,
                )
                if not retried:
                    raise RuntimeError("重试通知时丢失了通知 Outbox 锁")
                if attempt_no >= self.max_delivery_attempts:
                    await self._finish_candidate(
                        connection,
                        record=record,
                        status="DEAD",
                        error_code="NOTIFICATION_DELIVERY_FAILED",
                    )
            # 只有失败状态和 Outbox 重试状态一起提交后，才向监控系统报告本次失败。
            if self.metrics is not None and failed_attempt_status is not None:
                self.metrics.record_notification_delivery_attempt(channel, failed_attempt_status)
            return "retried"

    async def _finish_candidate(
        self,
        connection: object,
        *,
        record: NotificationOutboxRecord,
        status: str,
        error_code: str | None = None,
    ) -> None:
        candidate_id = record.payload.get("candidate_id")
        if not isinstance(candidate_id, str) or self.proactive_candidates is None:
            return
        await self.proactive_candidates.mark_notification_outcome(
            connection,
            candidate_id=candidate_id,
            status=status,
            error_code=error_code,
        )
