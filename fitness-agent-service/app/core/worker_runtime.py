"""所有常驻 Worker 共用的 SIGTERM 停领、批次 drain 与有界退出。"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from app.core.config import get_settings
from app.infrastructure.model_usage import collect_usage

logger = structlog.get_logger(__name__)
_stop: ContextVar[asyncio.Event | None] = ContextVar("worker_stop", default=None)
if TYPE_CHECKING:
    from app.core.metrics import HttpMetrics
_metrics: ContextVar[HttpMetrics | None] = ContextVar("worker_metrics", default=None)


async def poll_worker(
    run_once: Callable[[], Awaitable[Any]], *, interval: float, stop: asyncio.Event | None = None
) -> None:
    event = stop or _stop.get() or asyncio.Event()
    metrics = _metrics.get()
    if metrics:
        metrics.worker_ready.set(0 if event.is_set() else 1)
    try:
        while not event.is_set():
            with collect_usage(f"worker:{run_once.__module__}:{uuid4().hex}"):
                await run_once()  # SIGTERM 不直接取消在途批次。
            if event.is_set():
                return
            try:
                await asyncio.wait_for(event.wait(), timeout=interval)
            except TimeoutError:
                pass
    finally:
        if metrics:
            metrics.worker_ready.set(0)


async def _run_process(
    run: Callable[[], Coroutine[Any, Any, None]],
    *,
    timeout: float,
    metrics: HttpMetrics | None = None,
) -> None:
    event = asyncio.Event()
    token = _stop.set(event)
    metrics_token = _metrics.set(metrics)
    loop = asyncio.get_running_loop()

    def stop_claiming() -> None:
        if metrics:
            metrics.worker_ready.set(0)
        event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_claiming)
    task = asyncio.create_task(run())
    stopper = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            return
        logger.info("worker_draining", grace_seconds=timeout)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            logger.warning("worker_drain_deadline_exceeded")
            task.cancel()  # 事务回滚/租约过期负责恢复，不能声称 exactly-once。
            await asyncio.gather(task, return_exceptions=True)
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        _stop.reset(token)
        _metrics.reset(metrics_token)


def run_worker_process(
    run: Callable[[], Coroutine[Any, Any, None]], *, metrics: HttpMetrics | None = None
) -> None:
    asyncio.run(
        _run_process(run, timeout=get_settings().worker_shutdown_timeout_seconds, metrics=metrics)
    )
