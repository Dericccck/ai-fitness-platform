"""Memory 候选到期清理 Worker。"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.metrics import HttpMetrics

from .candidate_service import MemoryCandidateService

_logger = structlog.get_logger("memory.candidate_expiry_worker")
_WORKER_NAME = "memory_candidate_expiry"


@dataclass(frozen=True)
class MemoryCandidateExpiryRunResult:
    """一次有界清理轮询的结果。"""

    expired: int


class MemoryCandidateExpiryWorker:
    """定期把过期 PENDING 候选变为 EXPIRED。

    Worker 不放在 API 进程的后台 Task 中，而是通过独立进程启动。这样 API 重启不会
    丢失清理循环，生产环境也可以单独扩缩容。数据库层的 ``SKIP LOCKED`` 负责多实例
    并发安全；Worker 本身不读取候选正文，因此不会触发解密或泄露用户偏好。
    """

    def __init__(
        self,
        service: MemoryCandidateService,
        *,
        batch_size: int = 500,
        metrics: HttpMetrics | None = None,
    ) -> None:
        if batch_size < 1 or batch_size > 5000:
            raise ValueError("candidate expiry worker batch size must be between 1 and 5000")
        self.service = service
        self.batch_size = batch_size
        self.metrics = metrics

    async def run_once(self) -> MemoryCandidateExpiryRunResult:
        """执行一个有界批次，并记录低基数结果指标和结构化日志。"""

        try:
            expired = await self.service.expire_due(limit=self.batch_size)
        except Exception:
            if self.metrics is not None:
                self.metrics.maintenance_runs_total.labels(
                    worker=_WORKER_NAME, status="failed"
                ).inc()
            _logger.exception("memory_candidate_expiry_failed")
            raise
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            self.metrics.maintenance_items_total.labels(worker=_WORKER_NAME, outcome="expired").inc(
                expired
            )
            self.metrics.record_memory_candidate_event("expired", expired)
        _logger.info(
            "memory_candidate_expiry_batch",
            expired_count=expired,
            batch_size=self.batch_size,
        )
        return MemoryCandidateExpiryRunResult(expired=expired)
