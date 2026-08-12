"""长期运行的知识索引重建批次进程入口。"""

from __future__ import annotations

import asyncio

from app.main import app, lifespan


async def run() -> None:
    """复用 API 生命周期装配，但只轮询索引重建 Worker。"""

    async with lifespan(app):
        settings = app.state.settings
        worker = app.state.knowledge_reindex_worker
        while True:
            await worker.run_once()
            await asyncio.sleep(settings.rag_reindex_worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
