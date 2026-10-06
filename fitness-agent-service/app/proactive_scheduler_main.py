"""主动式 Agent 时间/状态候选调度进程。"""

from __future__ import annotations

from prometheus_client import start_http_server

from app.core.config import Settings
from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan
from app.proactive.scheduler import ProactiveCandidateScheduler


async def run() -> None:
    settings = Settings()
    if not settings.proactive_scheduler_enabled:
        raise RuntimeError("主动候选调度需要启用 PROACTIVE_SCHEDULER_ENABLED")
    start_http_server(settings.proactive_scheduler_metrics_port, registry=http_metrics.registry)
    async with lifespan(app):
        scheduler = ProactiveCandidateScheduler(
            app.state.database,
            app.state.proactive_candidates,
            inactivity_days=settings.proactive_inactivity_days,
            mismatch_skip_count=settings.proactive_mismatch_skip_count,
            default_timezone=settings.proactive_scheduler_timezone,
            digest_hour_local=settings.proactive_digest_hour_local,
        )
        await poll_worker(scheduler.run_once, interval=settings.proactive_scheduler_poll_seconds)


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
