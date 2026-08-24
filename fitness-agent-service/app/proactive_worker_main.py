"""主动提醒 Worker 进程入口。"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server

from app.core.config import Settings
from app.main import app, http_metrics, lifespan
from app.notifications.outbox import NotificationOutboxRepository
from app.proactive.rabbit_consumer import ProactiveRabbitConsumer
from app.proactive.repository import ProactiveEventRepository
from app.proactive.worker import ProactiveEventWorker


async def run() -> None:
    """同时运行 RabbitMQ Inbox 接收和 PostgreSQL Inbox 处理循环。"""

    settings = Settings()
    if not settings.proactive_worker_enabled:
        raise RuntimeError("PROACTIVE_WORKER_ENABLED must be true for proactive worker")
    start_http_server(settings.proactive_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        event_repository = ProactiveEventRepository()
        notification_repository = NotificationOutboxRepository()
        consumer = ProactiveRabbitConsumer(
            app.state.database,
            event_repository,
            url=settings.proactive_rabbitmq_url,
            exchange_name=settings.proactive_rabbitmq_exchange,
            queue_name=settings.proactive_rabbitmq_queue,
            routing_key=settings.proactive_rabbitmq_routing_key,
        )
        worker = ProactiveEventWorker(
            app.state.database,
            event_repository,
            notification_repository,
            batch_size=settings.proactive_worker_batch_size,
            metrics=http_metrics,
        )
        consumer_task = asyncio.create_task(consumer.run_forever())
        try:
            while True:
                await worker.run_once()
                await asyncio.sleep(settings.proactive_worker_poll_seconds)
        finally:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
