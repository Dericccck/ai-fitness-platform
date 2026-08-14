"""长期运行的 Memory 候选到期清理进程入口。"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server

from app.core.config import Settings
from app.main import app, http_metrics, lifespan


async def run() -> None:
    """复用统一基础设施装配，但只运行候选状态维护，不处理 HTTP 请求。"""

    settings = Settings()
    start_http_server(settings.memory_candidate_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        worker = app.state.memory_candidate_expiry_worker
        while True:
            await worker.run_once()
            await asyncio.sleep(settings.memory_candidate_expiry_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
