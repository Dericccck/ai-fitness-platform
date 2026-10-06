from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter
from typing import Any

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.model_usage import collect_usage
from app.streaming.repository import ChatRun, ChatRunRepository

logger = structlog.get_logger(__name__)
PUBLIC_EVENTS = frozenset(
    {
        "stage.changed",
        "answer.delta",
        "confirmation.required",
        "run.completed",
        "run.failed",
        "stream.reset",
    }
)


class ChatRunManager:
    """连接退出不取消业务任务；只重取终态，不重放图或重新调用模型。"""

    def __init__(
        self,
        repository: ChatRunRepository,
        *,
        timeout: float,
        heartbeat: float,
        queue_size: int,
        shutdown_timeout: float,
        metrics: HttpMetrics | None = None,
    ) -> None:
        self.repository = repository
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.queue_size = queue_size
        self.shutdown_timeout = shutdown_timeout
        self.metrics = metrics
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._sequence: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.closing = False

    async def start(
        self,
        run_id: str,
        owner: str,
        digest: str,
        request_id: str,
        execute: Callable[
            [Callable[[str, dict[str, Any]], Awaitable[None]]], Awaitable[dict[str, Any]]
        ],
    ) -> ChatRun:
        async with self._lock:
            if self.closing or len(self._tasks) >= 64:
                raise RuntimeError("任务服务繁忙")
            run, created = await self.repository.claim(run_id, owner, digest)
            if created:
                task = asyncio.create_task(self._execute(run_id, owner, request_id, execute))
                self._tasks[run_id] = task
                task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
            return run

    async def _execute(
        self,
        run_id: str,
        owner: str,
        request_id: str,
        execute: Callable[
            [Callable[[str, dict[str, Any]], Awaitable[None]]], Awaitable[dict[str, Any]]
        ],
    ) -> None:
        started = perf_counter()
        first_answer = False

        async def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal first_answer
            if event not in PUBLIC_EVENTS:
                return
            if not first_answer and event == "answer.delta" and data.get("text"):
                first_answer = True
                if self.metrics:
                    self.metrics.chat_first_answer_seconds.observe(perf_counter() - started)
            self._publish(run_id, event, data)

        try:
            with collect_usage(request_id, run_id=run_id):
                async with asyncio.timeout(self.timeout):
                    result = await execute(emit)
            async with asyncio.timeout(5):
                saved = await self.repository.finish(
                    run_id, owner, status="COMPLETED", result=result
                )
            if not saved:
                # 数据库终态先行；旧执行者不能用迟到的内存结果覆盖 UNKNOWN/FAILED。
                await emit("run.failed", {"status": "UNKNOWN", "error_code": "EXECUTION_LOST"})
                return
            if not first_answer and result.get("answer") and self.metrics:
                self.metrics.chat_first_answer_seconds.observe(perf_counter() - started)
            if result.get("status") == "CONFIRMATION_REQUIRED":
                await emit("confirmation.required", {"response": result})
            await emit("run.completed", {"response": result})
        except (Exception, asyncio.CancelledError) as exc:
            cancelled = isinstance(exc, (asyncio.CancelledError, TimeoutError))
            error_code = "EXECUTION_INTERRUPTED" if cancelled else "AGENT_UNAVAILABLE"
            status = "UNKNOWN" if cancelled else "FAILED"
            try:
                async with asyncio.timeout(3):
                    await self.repository.finish(
                        run_id, owner, status=status, error_code=error_code
                    )
            except Exception as persistence_error:  # noqa: BLE001 - 主故障不能被持久化错误覆盖
                logger.error(
                    "chat_run_persistence_failed", error_type=type(persistence_error).__name__
                )
            logger.warning("chat_run_failed", error_type=type(exc).__name__, request_id=request_id)
            await emit("run.failed", {"status": status, "error_code": error_code})
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._sequence.pop(run_id, None)

    def _publish(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        sequence = self._sequence.get(run_id, 0) + 1
        self._sequence[run_id] = sequence
        envelope = {"run_id": run_id, "sequence": sequence, "event": event, "data": data}
        if self.metrics:
            self.metrics.stream_events_total.labels(event=event).inc()
        for queue in tuple(self._subscribers.get(run_id, set())):
            if queue.full():
                # 慢客户端退出增量流并查询最终结果；绝不拖住模型或持久化。
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    {**envelope, "event": "stream.reset", "data": {"reason": "SLOW_CONSUMER"}}
                )
                self._subscribers[run_id].discard(queue)
            else:
                queue.put_nowait(envelope)

    async def events(self, run_id: str, owner: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            yield _sse({"run_id": run_id, "event": "run.started", "data": {}, "sequence": 0})
            while True:
                # 先订阅再读终态，避免完成事件落在注册订阅与查询之间而丢失。
                run = await self.repository.get(run_id, owner)
                if run is None:
                    yield _sse(
                        {
                            "run_id": run_id,
                            "event": "run.failed",
                            "data": {"error_code": "RUN_EXPIRED"},
                            "sequence": 0,
                        }
                    )
                    return
                if run.status != "RUNNING":
                    event = "run.completed" if run.status == "COMPLETED" else "run.failed"
                    data = (
                        {"response": run.result}
                        if run.result
                        else {"status": run.status, "error_code": run.error_code}
                    )
                    yield _sse({"run_id": run_id, "event": event, "data": data, "sequence": 0})
                    return
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=self.heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(envelope)
                if envelope["event"] in {"run.completed", "run.failed", "stream.reset"}:
                    return
        finally:
            subscribers = self._subscribers.get(run_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(run_id, None)

    async def close(self) -> None:
        self.closing = True
        tasks = list(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def _sse(envelope: dict[str, Any]) -> str:
    # 增量片段不持久化，不承诺 Last-Event-ID 精确回放；恢复时以加密终态为准。
    return f"event: {envelope['event']}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
