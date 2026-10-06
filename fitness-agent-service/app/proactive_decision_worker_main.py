"""长期运行的主动候选决策 Agent 进程。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan
from app.proactive.context_builder import ProactiveContextBuilder
from app.proactive.decision_agent import ProactiveDecisionAgent
from app.proactive.decision_worker import ProactiveDecisionWorker


async def run() -> None:
    settings = Settings()
    if not settings.proactive_decision_worker_enabled:
        raise RuntimeError("主动决策 Worker 需要启用 PROACTIVE_DECISION_WORKER_ENABLED")
    start_http_server(
        settings.proactive_decision_worker_metrics_port, registry=http_metrics.registry
    )
    async with lifespan(app):
        worker = ProactiveDecisionWorker(
            app.state.database,
            app.state.proactive_candidates,
            app.state.notification_outbox,
            ProactiveDecisionAgent(
                app.state.models,
                max_output_tokens=settings.proactive_decision_max_output_tokens,
                min_confidence=settings.proactive_decision_min_confidence,
                model_required=settings.proactive_decision_model_required,
            ),
            batch_size=settings.proactive_decision_batch_size,
            max_attempts=settings.proactive_decision_max_attempts,
            metrics=http_metrics,
            context_builder=ProactiveContextBuilder(app.state.database),
        )
        await poll_worker(worker.run_once, interval=settings.proactive_decision_poll_seconds)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
