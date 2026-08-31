"""Memory 正文保留期限治理 Worker。"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.metrics import HttpMetrics

from .retention import MemoryRetentionRepository

_logger = structlog.get_logger("memory.retention_worker")
_WORKER_NAME = "memory_retention"


@dataclass(frozen=True)
class MemoryRetentionWorkerRunResult:
    """一次有界清理轮询的结果。"""

    memories_redacted: int
    candidates_redacted: int


class MemoryRetentionWorker:
    """独立清理终态 Memory 正文，不读取或解密用户内容。"""

    def __init__(
        self,
        repository: MemoryRetentionRepository,
        *,
        batch_size: int = 500,
        metrics: HttpMetrics | None = None,
    ) -> None:
        if batch_size < 1 or batch_size > 5000:
            raise ValueError("Memory 保留 Worker 批次大小必须在 1 到 5000 之间")
        self.repository = repository
        self.batch_size = batch_size
        self.metrics = metrics

    async def run_once(self) -> MemoryRetentionWorkerRunResult:
        """清理一个有界批次，并记录低基数指标。"""

        try:
            result = await self.repository.redact_due(limit=self.batch_size)
        except Exception:
            if self.metrics is not None:
                self.metrics.maintenance_runs_total.labels(
                    worker=_WORKER_NAME, status="failed"
                ).inc()
            _logger.exception("memory_retention_batch_failed")
            raise
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="memory_redacted"
            ).inc(result.memories_redacted)
            self.metrics.maintenance_items_total.labels(
                worker=_WORKER_NAME, outcome="candidate_redacted"
            ).inc(result.candidates_redacted)
        _logger.info(
            "memory_retention_batch",
            memories_redacted=result.memories_redacted,
            candidates_redacted=result.candidates_redacted,
            batch_size=self.batch_size,
        )
        return MemoryRetentionWorkerRunResult(
            memories_redacted=result.memories_redacted,
            candidates_redacted=result.candidates_redacted,
        )
