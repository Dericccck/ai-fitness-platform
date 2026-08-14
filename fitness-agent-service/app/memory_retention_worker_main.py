"""长期运行的 Memory 正文保留期限治理进程入口。"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server

from app.core.config import Settings
from app.main import app, http_metrics, lifespan
from app.memory.retention import MemoryRetentionRepository
from app.memory.retention_worker import MemoryRetentionWorker


async def run() -> None:
    """复用统一数据库装配，但只运行数据治理循环，不处理 HTTP 请求。"""

    settings = Settings()
    start_http_server(settings.memory_retention_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        worker = MemoryRetentionWorker(
            MemoryRetentionRepository(app.state.database),
            batch_size=settings.memory_retention_batch_size,
            metrics=http_metrics,
        )
        while True:
            await worker.run_once()
            await asyncio.sleep(settings.memory_retention_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
