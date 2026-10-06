"""长期运行的经营决策动作 Worker。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.decision.action_worker import DecisionActionWorker
from app.main import app, http_metrics, lifespan


async def run() -> None:
    settings = Settings()
    if not settings.decision_action_worker_enabled:
        raise RuntimeError("决策动作 Worker 需要启用 DECISION_ACTION_WORKER_ENABLED")
    start_http_server(settings.decision_action_worker_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        worker = DecisionActionWorker(
            app.state.database,
            app.state.decision_repository,
            app.state.notification_outbox,
            batch_size=settings.decision_action_batch_size,
            max_attempts=settings.decision_action_max_attempts,
            metrics=http_metrics,
        )
        await poll_worker(worker.run_once, interval=settings.decision_action_poll_seconds)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
