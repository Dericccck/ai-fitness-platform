"""独立知识入库 Worker；待审核、安全与权限规则仍由既有服务执行。"""

from prometheus_client import start_http_server

from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan


async def run() -> None:
    async with lifespan(app):
        settings = app.state.settings
        start_http_server(settings.knowledge_worker_metrics_port, registry=http_metrics.registry)
        await poll_worker(
            app.state.knowledge_worker.run_once, interval=settings.knowledge_worker_poll_seconds
        )


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
