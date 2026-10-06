"""主动提醒 Worker 进程入口。"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan
from app.notifications.outbox import NotificationOutboxRepository
from app.proactive.candidate_engine import ProactiveCandidateEngine
from app.proactive.candidate_repository import ProactiveCandidateRepository
from app.proactive.rabbit_consumer import ProactiveRabbitConsumer
from app.proactive.repository import ProactiveEventRepository
from app.proactive.worker import ProactiveEventWorker


async def run() -> None:
    """同时运行 RabbitMQ Inbox 接收和 PostgreSQL Inbox 处理循环。"""

    settings = Settings()
    if not settings.proactive_worker_enabled:
        raise RuntimeError("主动事件 Worker 需要将 PROACTIVE_WORKER_ENABLED 设置为 true")
    start_http_server(settings.proactive_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        event_repository = ProactiveEventRepository()
        notification_repository = NotificationOutboxRepository()
        candidate_repository = ProactiveCandidateRepository()
        consumer = ProactiveRabbitConsumer(
            app.state.database,
            event_repository,
            url=settings.proactive_rabbitmq_url,
            exchange_name=settings.proactive_rabbitmq_exchange,
            queue_name=settings.proactive_rabbitmq_queue,
            routing_key=settings.proactive_rabbitmq_routing_key,
            reconnect_initial_seconds=settings.proactive_rabbitmq_reconnect_initial_seconds,
            reconnect_max_seconds=settings.proactive_rabbitmq_reconnect_max_seconds,
        )
        worker = ProactiveEventWorker(
            app.state.database,
            event_repository,
            notification_repository,
            batch_size=settings.proactive_worker_batch_size,
            metrics=http_metrics,
            candidate_engine=ProactiveCandidateEngine(app.state.database, candidate_repository),
        )
        consumer_task = asyncio.create_task(consumer.run_forever())
        try:
            await poll_worker(worker.run_once, interval=settings.proactive_worker_poll_seconds)
        finally:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
