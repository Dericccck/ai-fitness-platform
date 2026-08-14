"""短期会话摘要保留期限清理 Worker。"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.metrics import HttpMetrics

from .session_summary import SessionSummaryRepository

_logger = structlog.get_logger("agent.session_summary_worker")
_WORKER_NAME = "session_summary_retention"


@dataclass(frozen=True)
class SessionSummaryWorkerRunResult:
    """一次有界清理轮询的结果。"""

    deleted: int


class SessionSummaryCleanupWorker:
    """到期删除短期摘要，不读取、不解密摘要正文。"""

    def __init__(
        self,
        repository: SessionSummaryRepository,
        *,
        batch_size: int = 500,
        metrics: HttpMetrics | None = None,
    ) -> None:
        if batch_size < 1 or batch_size > 5000:
            raise ValueError("session summary batch size must be between 1 and 5000")
        self.repository = repository
        self.batch_size = batch_size
        self.metrics = metrics

    async def run_once(self) -> SessionSummaryWorkerRunResult:
        """清理一个批次，并使用低基数指标记录成功或失败。"""

        try:
            deleted = await self.repository.delete_due(limit=self.batch_size)
        except Exception:
            if self.metrics is not None:
                self.metrics.maintenance_runs_total.labels(
                    worker=_WORKER_NAME, status="failed"
                ).inc()
            _logger.exception("session_summary_retention_batch_failed")
            raise
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="summary_deleted"
            ).inc(deleted)
        _logger.info("session_summary_retention_batch", deleted=deleted, batch_size=self.batch_size)
        return SessionSummaryWorkerRunResult(deleted=deleted)
