"""确认执行结果对账 Worker。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from .repository import ConfirmationRepository

_logger = structlog.get_logger("agent.confirmation_reconciliation")


@dataclass(frozen=True)
class ConfirmationReconciliationRunResult:
    scanned: int
    marked_unknown: int


class ConfirmationReconciliationWorker:
    """将长期 RUNNING 标记为 UNKNOWN，等待下游查询或人工处置。

    Worker 不会把未查到的操作判定为失败，也不会生成新的 operation_id；后续对账入口
    仍使用确认单保存的稳定 request_id 查询下游。
    """

    def __init__(self, repository: ConfirmationRepository, *, older_than_seconds: int = 300, batch_size: int = 100) -> None:
        self.repository = repository
        self.older_than_seconds = older_than_seconds
        self.batch_size = batch_size

    async def run_once(self) -> ConfirmationReconciliationRunResult:
        records = await self.repository.list_stale_running(
            older_than_seconds=self.older_than_seconds, limit=self.batch_size
        )
        marked = 0
        for record in records:
            try:
                await self.repository.mark_unknown(
                    record.id, datetime.now(UTC), "reconciliation-worker"
                )
                marked += 1
            except Exception:
                _logger.exception("confirmation_reconciliation_failed", confirmation_id=record.id)
        return ConfirmationReconciliationRunResult(scanned=len(records), marked_unknown=marked)
