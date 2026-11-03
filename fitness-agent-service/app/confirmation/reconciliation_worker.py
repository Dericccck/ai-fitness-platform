"""确认执行结果对账 Worker；原子查询租约、持久化退避和小批并发。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from .repository import ConfirmationRepository

_logger = structlog.get_logger("agent.confirmation_reconciliation")


@dataclass(frozen=True)
class ConfirmationReconciliationRunResult:
    scanned: int
    marked_unknown: int
    reconciled: int
    query_failures: int = 0


class ConfirmationReconciliationWorker:
    """仅查询 Java 9 类写操作；未定结果退避，本地 Memory UNKNOWN 保留人工核实。"""

    def __init__(
        self,
        repository: ConfirmationRepository,
        *,
        reconciler: Any | None = None,
        older_than_seconds: int = 300,
        batch_size: int = 100,
        concurrency: int = 8,
        query_timeout_seconds: float = 30,
        lease_seconds: int = 60,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
    ) -> None:
        if not 1 <= batch_size <= 500 or not 1 <= concurrency <= 32 or older_than_seconds < 1:
            raise ValueError("对账 Worker 批量参数无效")
        if (
            not 0 < query_timeout_seconds <= 300
            or not query_timeout_seconds + 5 <= lease_seconds <= 3600
        ):
            raise ValueError("对账查询时限必须短于租约")
        if not 1 <= retry_base_seconds <= retry_max_seconds <= 86400:
            raise ValueError("对账退避参数无效")
        self.repository, self.reconciler = repository, reconciler
        self.older_than_seconds, self.batch_size = older_than_seconds, batch_size
        self.concurrency, self.query_timeout_seconds = concurrency, query_timeout_seconds
        self.lease_seconds = lease_seconds
        self.retry_base_seconds, self.retry_max_seconds = retry_base_seconds, retry_max_seconds

    async def run_once(self) -> ConfirmationReconciliationRunResult:
        scanned = marked = reconciled = failures = 0
        # 每波只领取马上处理的少量记录，避免在内存队列里等到租约过期。
        while scanned < self.batch_size:
            limit = min(self.concurrency, self.batch_size - scanned)
            records = await self.repository.claim_reconciliation_batch(
                older_than_seconds=self.older_than_seconds,
                limit=limit,
                lease_seconds=self.lease_seconds,
            )
            if not records:
                break
            outcomes = await asyncio.gather(*(self._process(record) for record in records))
            scanned += len(records)
            for mark, success, failure in outcomes:
                marked += mark
                reconciled += success
                failures += failure
            if len(records) < limit:
                break
        return ConfirmationReconciliationRunResult(scanned, marked, reconciled, failures)

    async def _process(self, record: Any) -> tuple[int, int, int]:
        marked = reconciled = failures = 0
        try:
            async with asyncio.timeout(self.query_timeout_seconds):
                current = record
                if getattr(record, "execution_status", "RUNNING") in {
                    "RUNNING",
                    "FAILED_RETRYABLE",
                }:
                    updated = await self.repository.mark_unknown(
                        record.id, datetime.now(UTC), "reconciliation-worker"
                    )
                    current = updated or record
                    marked = 1
                if self.reconciler is not None:
                    result = await self.reconciler.reconcile_stored_execution(
                        current, trace_id="reconciliation-worker"
                    )
                    reconciled = int(getattr(result, "execution_status", None) == "SUCCEEDED")
        except Exception as exc:  # noqa: BLE001 - 单条查询失败不能阻塞其他确认单
            failures = 1
            # 异常链可能含数据库绑定值；只记稳定类型，不记录异常正文或 traceback。
            _logger.warning("confirmation_reconciliation_failed", error_type=type(exc).__name__)
        finally:
            try:
                # A failed cleanup can safely expire its read-only lease. Never leave
                # an unbounded shielded database task behind during shutdown.
                async with asyncio.timeout(5):
                    await self.repository.finish_reconciliation_attempt(
                        record.id,
                        record.reconciliation_claim_token,
                        retry_base_seconds=self.retry_base_seconds,
                        retry_max_seconds=self.retry_max_seconds,
                    )
            except Exception as exc:  # noqa: BLE001 - 释放失败由持久化租约过期恢复
                _logger.warning(
                    "confirmation_reconciliation_schedule_failed", error_type=type(exc).__name__
                )
        return marked, reconciled, failures
