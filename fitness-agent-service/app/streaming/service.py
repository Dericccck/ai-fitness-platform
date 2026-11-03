from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter
from typing import Any

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.model_usage import collect_usage
from app.streaming.control import RunControl, run_control
from app.streaming.repository import ChatRun, ChatRunRepository

logger = structlog.get_logger(__name__)
PUBLIC_EVENTS = frozenset(
    {
        "stage.changed",
        "answer.delta",
        "confirmation.required",
        "run.completed",
        "run.failed",
        "run.cancel_requested",
        "run.cancelled",
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
        cancel_confirmations: Callable[[str], Awaitable[None]] | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.repository = repository
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.queue_size = queue_size
        self.shutdown_timeout = shutdown_timeout
        self.metrics = metrics
        self.cancel_confirmations = cancel_confirmations
        self.poll_interval = poll_interval
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._executions: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._controls: dict[str, RunControl] = {}
        self._watcher: asyncio.Task[None] | None = None
        self._lingering: set[asyncio.Task[dict[str, Any]]] = set()
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
        *,
        scope: str | None = None,
    ) -> ChatRun:
        async with self._lock:
            if self.closing or len(self._tasks) >= 64:
                raise RuntimeError("任务服务繁忙")
            run, created = await self.repository.claim(run_id, owner, digest, scope=scope)
            if created:
                self._controls[run_id] = RunControl(run_id, owner, self.repository)
                task = asyncio.create_task(self._execute(run_id, owner, request_id, execute))
                self._tasks[run_id] = task
                task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
                if self._watcher is None or self._watcher.done():
                    self._watcher = asyncio.create_task(self._watch_cancellations())
            return run

    async def cancel(self, run_id: str, owner: str, request_id: str) -> ChatRun | None:
        run = await self.repository.request_cancel(run_id, owner, request_id)
        if run is not None and run.status == "CANCEL_REQUESTED":
            control = self._controls.get(run_id)
            if control is not None and control.owner == owner:
                self._signal_cancel(run_id)
        return run

    def _signal_cancel(self, run_id: str, *, user_requested: bool = True) -> None:
        control = self._controls[run_id]
        if not control.cancelled:
            control.cancelled = True
            if user_requested:
                self._publish(run_id, "run.cancel_requested", {"status": "CANCEL_REQUESTED"})
        execution = self._executions.get(run_id)
        if execution is not None and not execution.done() and not execution.cancelling():
            execution.cancel()

    async def _watch_cancellations(self) -> None:
        while self._tasks:
            try:
                async with asyncio.timeout(2):
                    runs = await self.repository.controls(list(self._controls))
                for run_id in tuple(self._controls):
                    run = runs.get(run_id)
                    if run is None or run.status != "RUNNING":
                        self._signal_cancel(
                            run_id,
                            user_requested=(run is not None and run.status == "CANCEL_REQUESTED"),
                        )
            except Exception as exc:  # noqa: BLE001 - 控制面失联时停止而非继续执行
                # 控制事实不可读时停止执行，不能继续启动可能产生副作用的新步骤。
                logger.warning("chat_run_control_unavailable", error_type=type(exc).__name__)
                for run_id in tuple(self._controls):
                    self._signal_cancel(run_id, user_requested=False)
            await asyncio.sleep(self.poll_interval)

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
        control = self._controls[run_id]

        async def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal first_answer
            if event not in PUBLIC_EVENTS:
                return
            control.check()
            if not first_answer and event == "answer.delta" and data.get("text"):
                first_answer = True
                if self.metrics:
                    self.metrics.chat_first_answer_seconds.observe(perf_counter() - started)
            self._publish(run_id, event, data)

        async def run() -> dict[str, Any]:
            with collect_usage(request_id, run_id=run_id), run_control(control):
                await control.checkpoint()
                return await execute(emit)

        execution = asyncio.create_task(run())
        self._executions[run_id] = execution
        try:
            done, _ = await asyncio.wait({execution}, timeout=self.timeout)
            if not done:
                raise TimeoutError()
            result = execution.result()
            control.check()
            async with asyncio.timeout(5):
                saved = await self.repository.finish(
                    run_id, owner, status="COMPLETED", result=result, completion=control.completion
                )
            if not saved:
                async with asyncio.timeout(5):
                    await self._settle_cancel(run_id, owner, execution.done())
                    await self._publish_terminal(run_id, owner)
                return
            if not first_answer and result.get("answer") and self.metrics:
                self.metrics.chat_first_answer_seconds.observe(perf_counter() - started)
            if result.get("status") == "CONFIRMATION_REQUIRED":
                self._publish(run_id, "confirmation.required", {"response": result})
            self._publish(run_id, "run.completed", {"response": result})
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - 统一持久化运行终态
            if not execution.done():
                if not execution.cancelling():
                    execution.cancel()
                await asyncio.wait({execution}, timeout=5)
            cancelled = isinstance(exc, (asyncio.CancelledError, TimeoutError))
            error_code = "EXECUTION_INTERRUPTED" if cancelled else "AGENT_UNAVAILABLE"
            status = "UNKNOWN" if cancelled else "FAILED"
            try:
                async with asyncio.timeout(3):
                    await self.repository.finish(
                        run_id,
                        owner,
                        status=status,
                        error_code=error_code,
                        stop_reason=(
                            "EXECUTION_TIMEOUT"
                            if isinstance(exc, TimeoutError)
                            else "SERVICE_SHUTDOWN"
                            if self.closing
                            else "EXECUTION_INTERRUPTED"
                        )
                        if cancelled
                        else None,
                    )
                    await self._settle_cancel(run_id, owner, execution.done())
                    await self._publish_terminal(run_id, owner)
            except Exception as persistence_error:  # noqa: BLE001 - 主故障不能被持久化错误覆盖
                logger.error(
                    "chat_run_persistence_failed", error_type=type(persistence_error).__name__
                )
            logger.warning("chat_run_failed", error_type=type(exc).__name__, request_id=request_id)
        finally:
            if execution.done():
                # 取出子任务异常，避免 Task exception was never retrieved。
                await asyncio.gather(execution, return_exceptions=True)
            else:
                self._lingering.add(execution)
                execution.add_done_callback(self._lingering.discard)
                execution.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
            self._executions.pop(run_id, None)
            self._controls.pop(run_id, None)
            self._sequence.pop(run_id, None)

    async def _settle_cancel(self, run_id: str, owner: str, stopped: bool) -> None:
        run = await self.repository.get(run_id, owner)
        if run is not None and run.cancel_requested_at is not None and stopped:
            if self.cancel_confirmations is not None:
                await self.cancel_confirmations(run_id)
            if run.status == "CANCEL_REQUESTED":
                await self.repository.finish_cancelled(run_id, owner)

    async def _publish_terminal(self, run_id: str, owner: str) -> None:
        run = await self.repository.get(run_id, owner)
        if run is not None:
            event, data = _run_event(run)
            self._publish(run_id, event, data)

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
                if run.status not in {"RUNNING", "CANCEL_REQUESTED"}:
                    event, data = _run_event(run)
                    yield _sse({"run_id": run_id, "event": event, "data": data, "sequence": 0})
                    return
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=self.heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(envelope)
                if envelope["event"] in {
                    "run.completed",
                    "run.failed",
                    "run.cancelled",
                    "stream.reset",
                }:
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
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)


def _run_event(run: ChatRun) -> tuple[str, dict[str, Any]]:
    event = {
        "COMPLETED": "run.completed",
        "CANCELLED": "run.cancelled",
        "CANCEL_REQUESTED": "run.cancel_requested",
    }.get(run.status, "run.failed")
    return event, (
        {"response": run.result}
        if run.result
        else {
            "status": run.status,
            "error_code": run.error_code,
            "stop_reason": run.stop_reason,
        }
    )


def _sse(envelope: dict[str, Any]) -> str:
    # 增量片段不持久化，不承诺 Last-Event-ID 精确回放；恢复时以加密终态为准。
    return f"event: {envelope['event']}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
