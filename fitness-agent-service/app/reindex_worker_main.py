"""Long-running process entrypoint for knowledge index rebuild batches."""

from __future__ import annotations

import asyncio

from app.main import app, lifespan


async def run() -> None:
    """Reuse the API lifecycle wiring while polling only the rebuild worker."""

    async with lifespan(app):
        settings = app.state.settings
        worker = app.state.knowledge_reindex_worker
        while True:
            await worker.run_once()
            await asyncio.sleep(settings.rag_reindex_worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
