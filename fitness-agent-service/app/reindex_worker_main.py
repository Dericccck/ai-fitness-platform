"""长期运行的知识索引重建批次进程入口。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan


async def run() -> None:
    """复用 API 生命周期装配，但只轮询索引重建 Worker。"""

    async with lifespan(app):
        settings = app.state.settings
        start_http_server(settings.knowledge_worker_metrics_port, registry=http_metrics.registry)
        worker = app.state.knowledge_reindex_worker
        await poll_worker(worker.run_once, interval=settings.rag_reindex_worker_poll_seconds)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
