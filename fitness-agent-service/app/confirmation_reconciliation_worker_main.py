"""确认单执行结果对账进程入口。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.confirmation.reconciliation_worker import ConfirmationReconciliationWorker
from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan


async def run() -> None:
    """复用统一数据库生命周期，持续扫描长时间 RUNNING 确认单。"""

    settings = Settings()
    start_http_server(
        settings.confirmation_reconciliation_worker_metrics_port,
        registry=http_metrics.registry,
    )
    async with lifespan(app):
        worker = ConfirmationReconciliationWorker(
            app.state.confirmation_service.repository,
            reconciler=app.state.confirmation_service,
            older_than_seconds=max(60, settings.confirmation_ttl_seconds // 2),
            batch_size=settings.confirmation_reconciliation_batch_size,
        )
        await poll_worker(
            worker.run_once, interval=settings.confirmation_reconciliation_poll_seconds
        )


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
