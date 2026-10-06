"""短期会话摘要保留期限治理进程入口。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan
from app.session_summary import SessionSummaryRepository
from app.session_summary_worker import SessionSummaryCleanupWorker


async def run() -> None:
    """复用统一数据库生命周期，只轮询摘要到期删除任务。"""

    settings = Settings()
    start_http_server(settings.session_summary_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        worker = SessionSummaryCleanupWorker(
            SessionSummaryRepository(app.state.database),
            batch_size=settings.session_summary_batch_size,
            metrics=http_metrics,
        )
        await poll_worker(worker.run_once, interval=settings.session_summary_poll_seconds)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
