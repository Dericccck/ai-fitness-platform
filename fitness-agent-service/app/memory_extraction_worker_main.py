"""异步 Memory 候选提取进程。"""

from __future__ import annotations

from time import monotonic

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan


async def run() -> None:
    settings = Settings()
    start_http_server(
        settings.memory_extraction_worker_metrics_port, registry=http_metrics.registry
    )
    async with lifespan(app, role="worker"):
        next_cleanup = 0.0

        async def maintain() -> None:
            nonlocal next_cleanup
            if settings.memory_async_extraction_enabled:
                await app.state.memory_extraction_worker.run_once()
            if monotonic() >= next_cleanup:
                await app.state.memory_service.expire_due(
                    limit=settings.memory_retention_batch_size
                )
                await app.state.memory_candidate_service.expire_due(
                    limit=settings.memory_candidate_expiry_batch_size
                )
                await app.state.memory_source_retention.cleanup_expired(
                    limit=settings.memory_retention_batch_size
                )
                next_cleanup = monotonic() + 300

        await poll_worker(
            maintain,
            interval=settings.memory_extraction_poll_seconds,
        )


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
