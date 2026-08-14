"""长期运行的站内通知 Outbox 发布进程入口。"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server

from app.core.config import Settings
from app.main import app, http_metrics, lifespan
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.preferences import NotificationPreferenceRepository
from app.notifications.worker import NotificationOutboxWorker


async def run() -> None:
    """启动独立通知 Worker，不把轮询任务放进 API 进程。"""

    settings = Settings()
    start_http_server(settings.notification_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        worker = NotificationOutboxWorker(
            app.state.database,
            NotificationOutboxRepository(),
            batch_size=settings.notification_worker_batch_size,
            preferences=NotificationPreferenceRepository(
                default_timezone=settings.notification_default_timezone
            ),
            metrics=http_metrics,
        )
        while True:
            await worker.run_once()
            await asyncio.sleep(settings.notification_worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
