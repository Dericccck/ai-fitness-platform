import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.supervisor import Supervisor
from app.agent.tool_registry import ToolRegistry
from app.infrastructure.model_gateway import ModelGateway, ModelTurn
from app.streaming.completion import HistoryCommit
from app.streaming.events import AnswerFilter, emit_event
from app.streaming.repository import ChatRun, RunConflict, RunSessionBusy
from app.streaming.service import ChatRunManager
from tests.test_supervisor import FakeModels, request


class MemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, tuple[str, str, ChatRun]] = {}
        self.scopes: dict[str, str | None] = {}
        self.commits: dict[tuple[str, str], HistoryCommit] = {}

    async def claim(
        self, run_id: str, owner: str, digest: str, *, scope: str | None = None
    ) -> tuple[ChatRun, bool]:
        if run_id in self.runs:
            old_owner, old_digest, run = self.runs[run_id]
            if (old_owner, old_digest) != (owner, digest):
                raise RunConflict()
            return run, False
        if scope and any(
            self.scopes.get(key) == scope and item[2].status in {"RUNNING", "CANCEL_REQUESTED"}
            for key, item in self.runs.items()
        ):
            raise RunSessionBusy()
        run = ChatRun(run_id, "RUNNING")
        self.runs[run_id] = (owner, digest, run)
        self.scopes[run_id] = scope
        return run, True

    async def get(self, run_id: str, owner: str) -> ChatRun | None:
        item = self.runs.get(run_id)
        return item[2] if item and item[0] == owner else None

    async def finish(self, run_id: str, owner: str, **fields: Any) -> bool:
        old_owner, digest, run = self.runs[run_id]
        assert old_owner == owner
        if run.status != "RUNNING":
            return False
        completion = fields.pop("completion", None)
        if fields["status"] == "COMPLETED" and completion is not None:
            key = (completion.thread_id, owner)
            old = self.commits.get(key)
            assert (old.version if old else 0) == completion.base_version
            self.commits[key] = HistoryCommit(run_id, completion.base_version + 1)
        self.runs[run_id] = (owner, digest, ChatRun(run_id, **fields))
        return True

    async def history_commit(
        self, thread_id: str, owner: str, subject: str, prior_run_id: str | None = None
    ) -> HistoryCommit | None:
        key = (thread_id, owner)
        prior = await self.get(prior_run_id, owner) if prior_run_id else None
        if key not in self.commits and prior is not None and prior.status == "COMPLETED":
            self.commits[key] = HistoryCommit(prior.run_id, 1)
        return self.commits.get(key)

    async def request_cancel(self, run_id: str, owner: str, request_id: str) -> ChatRun | None:
        run = await self.get(run_id, owner)
        if run is not None and run.status == "RUNNING":
            _, digest, _ = self.runs[run_id]
            run = replace(
                run,
                status="CANCEL_REQUESTED",
                stop_reason="USER_CANCELLED",
                cancel_requested_at=datetime.now(UTC),
            )
            self.runs[run_id] = (owner, digest, run)
        return run

    async def finish_cancelled(self, run_id: str, owner: str) -> bool:
        run = await self.get(run_id, owner)
        if run is None or run.status != "CANCEL_REQUESTED":
            return False
        _, digest, _ = self.runs[run_id]
        self.runs[run_id] = (
            owner,
            digest,
            replace(run, status="CANCELLED", cancelled_at=datetime.now(UTC)),
        )
        return True

    async def controls(self, run_ids: list[str]) -> dict[str, ChatRun]:
        return {key: self.runs[key][2] for key in run_ids if key in self.runs}


def manager(repo: MemoryRunRepository) -> ChatRunManager:
    return ChatRunManager(
        cast(Any, repo),
        timeout=2,
        heartbeat=0.01,
        queue_size=8,
        shutdown_timeout=0.1,
        poll_interval=0.01,
    )


def test_split_protocol_marker_is_never_exposed() -> None:
    filter_ = AnswerFilter()
    result = filter_.push("安全回答<｜｜DS") + filter_.push("ML｜｜tool_calls>私密参数")
    result += filter_.push("更多内容", final=True)
    assert result == "安全回答"


async def test_langgraph_custom_events_use_same_checkpoint_and_final_answer() -> None:
    class StreamModels(FakeModels):
        async def chat_with_tools(self, *args: Any, **kwargs: Any) -> ModelTurn:
            emit_event("answer.delta", {"text": "热身建议"})
            return await super().chat_with_tools(*args, **kwargs)

    saver = InMemorySaver()
    supervisor = Supervisor(
        cast(ModelGateway, StreamModels([ModelTurn("热身建议", (), 4, 3)])),
        ToolRegistry(),
        checkpointer=saver,
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    result = await supervisor.stream(request("如何热身"), emit)
    assert result.answer == "热身建议"
    assert ("answer.delta", {"text": "热身建议"}) in events
    state = await supervisor._graph.aget_state({"configurable": {"thread_id": "conversation-1"}})
    assert state.values["final_answer"] == result.answer
    assert "gateway_context" not in state.values


async def test_repeat_run_and_foreign_owner_never_execute_twice() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    execute = AsyncMock(return_value={"answer": "结果"})
    await runs.start("a", "owner", "digest", "request", execute)
    await runs.start("a", "owner", "digest", "request", execute)
    with pytest.raises(RunConflict):
        await runs.start("a", "owner", "changed", "request", execute)
    assert await repo.get("a", "foreign") is None
    await runs.close()
    execute.assert_awaited_once()
    assert (await repo.get("a", "owner")).status == "COMPLETED"  # type: ignore[union-attr]


async def test_client_disconnect_does_not_cancel_job_and_result_is_recoverable() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    proceed = asyncio.Event()

    async def execute(emit: Any) -> dict[str, Any]:
        await proceed.wait()
        return {"answer": "已生成确认卡", "status": "CONFIRMATION_REQUIRED", "confirmation_id": "c"}

    await runs.start("a", "owner", "digest", "request", execute)
    stream = runs.events("a", "owner")
    assert "run.started" in await anext(stream)
    await stream.aclose()
    proceed.set()
    await runs.close()
    result = await repo.get("a", "owner")
    assert result is not None and result.result is not None
    assert result.result["confirmation_id"] == "c"
    restored = [item async for item in runs.events("a", "owner")]
    assert "run.completed" in restored[-1]


async def test_shutdown_deadline_marks_unknown_without_automatic_reexecution() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)

    async def execute(emit: Any) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}

    await runs.start("a", "owner", "digest", "request", execute)
    await asyncio.sleep(0)
    await runs.close()
    result = await repo.get("a", "owner")
    assert result is not None and result.status == "UNKNOWN"
    assert result.error_code == "EXECUTION_INTERRUPTED"


async def test_slow_subscriber_is_reset_without_blocking_producer() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    stream = runs.events("a", "owner")
    await anext(stream)
    for _ in range(20):
        runs._publish("a", "answer.delta", {"text": "片段"})
    queue = runs._subscribers["a"]
    assert len(queue) == 0
    await stream.aclose()
