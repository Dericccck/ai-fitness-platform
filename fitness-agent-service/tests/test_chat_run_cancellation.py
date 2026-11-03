import asyncio
from dataclasses import replace
from time import perf_counter
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.supervisor import Supervisor
from app.agent.tool_registry import ToolDefinition, ToolRegistry
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn
from app.rag.service import RagService
from app.streaming.control import RunControl, current_control, run_owner
from app.streaming.repository import ChatRun, RunSessionBusy
from tests.test_rag import FakeModels as EmbedModels
from tests.test_rag import scope
from tests.test_runtime_sdk_stream import FakeStream, chunk
from tests.test_runtime_streaming import MemoryRunRepository, manager
from tests.test_supervisor import FakeModels, ToolInput, request


async def test_cancel_waits_for_cleanup_and_repeated_cancel_does_not_interrupt_it() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    following = AsyncMock()

    async def execute(emit: Any) -> dict[str, Any]:
        entered.set()
        try:
            await asyncio.Event().wait()
            await following()
        finally:
            cleanup.set()
            await release.wait()
        return {"answer": "late"}

    await runs.start("a", "owner", "digest", "request", execute, scope="conversation")
    task = runs._tasks["a"]
    await entered.wait()
    stream = runs.events("a", "owner")
    await anext(stream)
    assert (await runs.cancel("a", "owner", "stop")).status == "CANCEL_REQUESTED"
    await cleanup.wait()
    assert "run.cancel_requested" in await anext(stream)
    for _ in range(3):
        assert (await runs.cancel("a", "owner", "stop")).status == "CANCEL_REQUESTED"
    assert not task.done()
    with pytest.raises(RunSessionBusy):
        await runs.start("b", "owner", "d", "r", execute, scope="conversation")
    release.set()
    await asyncio.wait_for(task, 1)
    assert (await repo.get("a", "owner")).status == "CANCELLED"
    assert "run.cancelled" in await anext(stream)
    following.assert_not_awaited()
    assert (await runs.cancel("a", "owner", "other")).status == "CANCELLED"
    await stream.aclose()
    await runs.close()


async def test_cancel_on_another_instance_is_found_without_redis_notification() -> None:
    repo = MemoryRunRepository()
    a, b = manager(repo), manager(repo)
    entered = asyncio.Event()

    async def execute(emit: Any) -> dict[str, Any]:
        entered.set()
        await asyncio.Event().wait()
        return {}

    await a.start("a", "owner", "digest", "request", execute)
    task = a._tasks["a"]
    await entered.wait()
    assert await b.cancel("a", "stranger", "stop") is None
    assert (await b.cancel("a", "owner", "stop")).status == "CANCEL_REQUESTED"
    await asyncio.wait_for(task, 1)
    assert (await repo.get("a", "owner")).status == "CANCELLED"
    await a.close()
    await b.close()


async def test_cancel_before_execution_starts_never_calls_agent() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    execute = AsyncMock(return_value={"answer": "late"})
    await runs.start("a", "owner", "digest", "request", execute)
    assert (await runs.cancel("a", "owner", "stop")).status == "CANCEL_REQUESTED"
    await runs.close()
    execute.assert_not_awaited()
    assert (await repo.get("a", "owner")).status == "CANCELLED"


async def test_cancel_winning_completion_cas_publishes_canonical_cancelled_state() -> None:
    class RacingRepository(MemoryRunRepository):
        async def finish(self, run_id: str, owner: str, **fields: Any) -> bool:
            if fields["status"] == "COMPLETED":
                await self.request_cancel(run_id, owner, "stop")
            return await super().finish(run_id, owner, **fields)

    repo = RacingRepository()
    runs = manager(repo)
    cleanup = AsyncMock()
    runs.cancel_confirmations = cleanup
    await runs.start("a", "owner", "digest", "request", AsyncMock(return_value={"answer": "late"}))
    await runs.close()
    assert (await repo.get("a", "owner")).status == "CANCELLED"
    cleanup.assert_awaited_once_with("a")
    stream = [event async for event in runs.events("a", "owner")]
    assert "run.cancelled" in stream[-1]
    assert all("run.completed" not in event for event in stream)


async def test_completion_wins_and_cancellation_never_overwrites_it() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    await runs.start("a", "owner", "digest", "request", AsyncMock(return_value={"answer": "done"}))
    await runs.close()
    run = await runs.cancel("a", "owner", "stop")
    assert run.status == "COMPLETED" and run.result == {"answer": "done"}


async def test_child_signal_inherits_parent_but_does_not_cancel_parent() -> None:
    parent = RunControl("a", "owner", cast(Any, MemoryRunRepository()))
    child = parent.child()
    child.cancelled = True
    parent.check()
    with pytest.raises(asyncio.CancelledError):
        child.check()
    child.cancelled = False
    parent.cancelled = True
    with pytest.raises(asyncio.CancelledError):
        child.check()


@pytest.mark.parametrize("status", ["CANCELLED", "UNKNOWN", "FAILED"])
async def test_next_turn_uses_complete_history_not_unpaired_tool_calls(status: str) -> None:
    identity = AgentIdentity("user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 2)
    owner = run_owner(identity)
    repo = MemoryRunRepository()
    repo.runs["old"] = (owner, "digest", ChatRun("old", status))
    models = FakeModels([ModelTurn("新回答", ())])
    supervisor = Supervisor(
        cast(ModelGateway, models),
        ToolRegistry(),
        checkpointer=InMemorySaver(),
        chat_run_repository=cast(Any, repo),
    )
    base = [{"role": "user", "content": "完整问题"}, {"role": "assistant", "content": "完整回答"}]
    await supervisor._graph.aupdate_state(
        {"configurable": {"thread_id": "conversation-1"}},
        {
            "turn_run_id": "old",
            "turn_base_messages": base,
            "messages": [
                *base,
                {"role": "user", "content": "已取消问题"},
                {
                    "role": "assistant",
                    "content": "半截回答",
                    "tool_calls": [
                        {
                            "id": "unpaired",
                            "type": "function",
                            "function": {"name": "tool", "arguments": "{}"},
                        }
                    ],
                },
            ],
        },
    )
    result = await supervisor.invoke(replace(request("如何热身"), identity=identity))
    assert result.answer == "新回答"
    contents = str(models.messages_seen)
    assert "完整问题" in contents and "完整回答" in contents
    assert (
        "已取消问题" not in contents and "半截回答" not in contents and "unpaired" not in contents
    )


async def test_run_control_is_available_in_execution_but_never_in_checkpoint() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    saver = InMemorySaver()
    supervisor = Supervisor(
        cast(ModelGateway, FakeModels([ModelTurn("回答", ())])), ToolRegistry(), checkpointer=saver
    )

    async def execute(emit: Any) -> dict[str, Any]:
        assert current_control().run_id == "a"
        response = await supervisor.stream(request("如何热身"), emit)
        return {"answer": response.answer}

    await runs.start("a", "owner", "digest", "request", execute)
    await runs.close()
    state = await supervisor._graph.aget_state({"configurable": {"thread_id": "conversation-1"}})
    assert state.values["turn_run_id"] == "a"
    assert "RunControl" not in str(state.values) and "signed-context" not in str(state.values)
    assert current_control() is None


async def test_cancel_waits_for_langgraph_tool_cleanup_and_no_next_model_call() -> None:
    repo = MemoryRunRepository()
    runs = manager(repo)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    models = FakeModels(
        [
            ModelTurn("", (ModelToolCall("call", "fitness.course.list.v1", {"value": "课程"}),)),
            ModelTurn("不应生成", ()),
        ]
    )
    tools = ToolRegistry()

    async def handler(raw: Any, context: Any) -> dict[str, str]:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
        return {}

    tools.register(
        ToolDefinition(
            tool_id="fitness.course.list.v1",
            description="测试只读工具",
            input_model=ToolInput,
            handler=handler,
            allowed_roles=frozenset({"STUDENT"}),
            read_only=True,
            requires_confirmation=False,
        )
    )
    supervisor = Supervisor(cast(ModelGateway, models), tools, checkpointer=InMemorySaver())

    async def execute(emit: Any) -> dict[str, Any]:
        result = await supervisor.stream(request(), emit)
        return {"answer": result.answer}

    await runs.start("a", "owner", "digest", "request", execute)
    task = runs._tasks["a"]
    await asyncio.wait_for(entered.wait(), 1)
    await runs.cancel("a", "owner", "stop")
    await asyncio.wait_for(cleaning.wait(), 1)
    assert not task.done()
    assert (await repo.get("a", "owner")).status == "CANCEL_REQUESTED"
    release.set()
    await asyncio.wait_for(task, 1)
    assert (await repo.get("a", "owner")).status == "CANCELLED"
    assert len(models.messages_seen) == 1
    await runs.close()


async def test_cancel_waits_for_both_parallel_rag_branches_to_clean_up() -> None:
    entered = [asyncio.Event(), asyncio.Event()]
    cleaning = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def recall(index: int, *args: Any, **kwargs: Any) -> list[Any]:
        entered[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[index].set()
            await release.wait()
        return []

    class Repository:
        async def search_candidates(self, *args: Any, **kwargs: Any) -> list[Any]:
            return await recall(0)

        async def search_keyword_candidates(self, *args: Any, **kwargs: Any) -> list[Any]:
            return await recall(1)

    service = RagService(cast(Any, Repository()), cast(Any, EmbedModels()), cast(Any, None))
    repo = MemoryRunRepository()
    runs = manager(repo)

    async def execute(emit: Any) -> dict[str, Any]:
        await service.search("如何热身", scope())
        return {}

    await runs.start("a", "owner", "digest", "request", execute)
    task = runs._tasks["a"]
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
    await runs.cancel("a", "owner", "stop")
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleaning)), 1)
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 1)
    assert (await repo.get("a", "owner")).status == "CANCELLED"
    await runs.close()


async def test_cancel_closes_provider_stream_without_another_attempt() -> None:
    entered = asyncio.Event()

    class WaitingStream(FakeStream):
        async def __anext__(self) -> Any:
            if self.chunks:
                return await super().__anext__()
            entered.set()
            await asyncio.Event().wait()

    model = ModelGateway(Settings(_env_file=None))
    stream = WaitingStream([chunk({"content": "部分答案"})])
    model._llm.chat.completions.create = AsyncMock(return_value=stream)
    repo = MemoryRunRepository()
    runs = manager(repo)

    async def execute(emit: Any) -> dict[str, Any]:
        await model._stream_completion({}, started=perf_counter())
        return {}

    try:
        await runs.start("a", "owner", "digest", "request", execute)
        task = runs._tasks["a"]
        await asyncio.wait_for(entered.wait(), 1)
        await runs.cancel("a", "owner", "stop")
        await asyncio.wait_for(task, 1)
        assert stream.closed
        model._llm.chat.completions.create.assert_awaited_once()
        assert (await repo.get("a", "owner")).status == "CANCELLED"
    finally:
        await runs.close()
        await model.close()


async def test_completed_turn_survives_result_retention_cleanup() -> None:
    identity = AgentIdentity("review-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 2)
    owner = run_owner(identity)
    repo = MemoryRunRepository()
    runs = manager(repo)
    models = FakeModels([ModelTurn("完整热身建议", ()), ModelTurn("后续回答", ())])
    supervisor = Supervisor(
        cast(Any, models),
        ToolRegistry(),
        checkpointer=InMemorySaver(),
        chat_run_repository=cast(Any, repo),
    )

    async def execute(emit: Any) -> dict[str, Any]:
        response = await supervisor.stream(
            replace(request("上一轮完整问题"), identity=identity), emit
        )
        return {"answer": response.answer}

    await runs.start("completed", owner, "d", "r", execute)
    await runs.close()
    assert (await repo.get("completed", owner)).status == "COMPLETED"
    del repo.runs["completed"]
    await supervisor.invoke(replace(request("继续上一轮"), identity=identity))
    assert "上一轮完整问题" in str(models.messages_seen[-1])
    assert "完整热身建议" in str(models.messages_seen[-1])


async def test_cancel_after_summary_preparation_preserves_previous_summary() -> None:
    from app.confirmation.cipher import AesGcmPayloadCipher
    from app.session_summary import SessionSummaryService
    from tests.test_session_summary import FakeModels as SummaryModels
    from tests.test_session_summary import FakeRepository

    class RaceRepository(MemoryRunRepository):
        async def finish(self, run_id: str, owner: str, **fields: Any) -> bool:
            if run_id == "cancelled" and fields["status"] == "COMPLETED":
                await self.request_cancel(run_id, owner, "stop-at-completion")
            return await super().finish(run_id, owner, **fields)

    identity = AgentIdentity("review-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 2)
    owner = run_owner(identity)
    repo = RaceRepository()
    runs = manager(repo)
    summary_repo = FakeRepository()
    summary = SessionSummaryService(
        cast(Any, SummaryModels('{"summary":"取消轮独有内容"}')),
        cast(Any, summary_repo),
        AesGcmPayloadCipher(b"x" * 32, "test-v1"),
        trigger_messages=2,
        keep_recent_messages=1,
    )
    models = FakeModels([ModelTurn("取消轮回答", ()), ModelTurn("下一轮回答", ())])
    supervisor = Supervisor(
        cast(Any, models),
        ToolRegistry(),
        checkpointer=InMemorySaver(),
        chat_run_repository=cast(Any, repo),
        session_summary_service=summary,
    )
    await supervisor._graph.aupdate_state(
        {"configurable": {"thread_id": "conversation-1"}},
        {
            "messages": [
                {"role": "user", "content": "可信问题"},
                {"role": "assistant", "content": "可信回答"},
            ]
        },
    )

    async def execute(emit: Any) -> dict[str, Any]:
        response = await supervisor.stream(
            replace(request("取消轮独有内容"), identity=identity), emit
        )
        return {"answer": response.answer}

    await runs.start("cancelled", owner, "d", "r", execute)
    await runs.close()
    assert (await repo.get("cancelled", owner)).status == "CANCELLED"
    assert summary_repo.upsert_calls == []
    await supervisor.invoke(replace(request("换个问题"), identity=identity))
    assert "取消轮独有内容" not in str(models.messages_seen[-1])


@pytest.mark.parametrize("generate_error", [False, True])
async def test_summary_failure_or_skipped_threshold_keeps_completed_answer(
    generate_error: bool,
) -> None:
    class Summaries:
        async def prepare_summary(self, **kwargs: Any) -> None:
            if generate_error:
                raise RuntimeError("auxiliary summary failure")

    identity = AgentIdentity("review-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 2)
    repo = MemoryRunRepository()
    runs = manager(repo)
    supervisor = Supervisor(
        cast(Any, FakeModels([ModelTurn("完整回答", ())])),
        ToolRegistry(),
        checkpointer=InMemorySaver(),
        session_summary_service=cast(Any, Summaries()),
    )

    async def execute(emit: Any) -> dict[str, Any]:
        answer = await supervisor.stream(replace(request("问题"), identity=identity), emit)
        return {"answer": answer.answer}

    owner = run_owner(identity)
    await runs.start("summary-failure", owner, "d", "r", execute)
    await runs.close()
    final = await repo.get("summary-failure", owner)
    assert final.status == "COMPLETED" and final.result["answer"] == "完整回答"
    assert (
        await repo.history_commit("conversation-1", owner, identity.subject)
    ).run_id == final.run_id
