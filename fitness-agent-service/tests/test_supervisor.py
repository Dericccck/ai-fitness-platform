from typing import Any, cast

import pytest
from langgraph.checkpoint.base import CheckpointTuple, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

from app.agent.supervisor import (
    Supervisor,
    SupervisorCheckpointIncompatible,
    SupervisorRequest,
    SupervisorRuntimeError,
    UnsupportedLegacyRequest,
    classify_route,
)
from app.agent.tool_registry import ToolContext, ToolDefinition, ToolRegistry
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn
from app.memory.candidate import MemoryCandidate


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class FakeModels:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns
        self.tools_seen: list[list[dict[str, Any]]] = []
        self.messages_seen: list[list[dict[str, Any]]] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        self.tools_seen.append(tools)
        self.messages_seen.append(messages)
        return self.turns.pop(0)


class FakeCandidateExtractor:
    async def propose(self, _: str) -> tuple[MemoryCandidate, ...]:
        return (
            MemoryCandidate(
                memory_type="EQUIPMENT_AVAILABILITY",
                memory_key="available_equipment",
                value="弹力带",
            ),
        )


class FakeSessionSummaryService:
    keep_recent_messages = 2

    async def load_for_subject(self, _: str, __: str) -> str:
        return "用户目标是改善体能；动态课程需要重新查询。"

    async def maybe_summarize(self, **_: Any) -> str:
        return "用户目标是改善体能；动态课程需要重新查询。"


def build_registry(calls: list[dict[str, Any]]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(raw: BaseModel, context: ToolContext) -> dict[str, str]:
        data = cast(ToolInput, raw)
        calls.append(
            {"value": data.value, "signed_context": context.gateway_context.signed_context}
        )
        return {"status": "真实查询结果", "value": data.value}

    registry.register(
        ToolDefinition(
            tool_id="fitness.test.lookup.v1",
            description="测试用的健身只读查询工具",
            input_model=ToolInput,
            handler=handler,
            allowed_roles=frozenset({"STUDENT"}),
            read_only=True,
            requires_confirmation=False,
        )
    )
    return registry


def request(message: str = "查询我的课程") -> SupervisorRequest:
    return SupervisorRequest(
        user_message=message,
        gateway_context=GatewayRequestContext(
            signed_context="signed-context",
            request_id="request-1",
            trace_id="trace-1",
        ),
        conversation_id="conversation-1",
    )


async def test_supervisor_runs_model_tool_model_cycle_and_returns_real_result() -> None:
    calls: list[dict[str, Any]] = []
    models = FakeModels(
        [
            ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-1",
                        name="fitness.test.lookup.v1",
                        arguments={"value": "课程"},
                    ),
                ),
                input_tokens=10,
                output_tokens=4,
            ),
            ModelTurn(
                content="已查询到真实课程结果。",
                tool_calls=(),
                input_tokens=8,
                output_tokens=6,
            ),
        ]
    )
    supervisor = Supervisor(
        cast(ModelGateway, models),
        build_registry(calls),
        max_tool_steps=1,
    )

    response = await supervisor.invoke(request())

    assert response.answer == "已查询到真实课程结果。"
    assert response.route == "BOOKING"
    assert response.tool_steps == 1
    assert response.input_tokens == 18
    assert response.output_tokens == 10
    assert calls == [{"value": "课程", "signed_context": "signed-context"}]
    # 达到预算后第二回合不再暴露任何工具，只允许模型总结真实结果。
    assert len(models.tools_seen) == 2
    assert models.tools_seen[0]
    assert models.tools_seen[1] == []


async def test_supervisor_uses_session_summary_after_checkpoint_compaction() -> None:
    models = FakeModels(
        [
            ModelTurn(content="第一轮回答。", tool_calls=()),
            ModelTurn(content="第二轮回答。", tool_calls=()),
        ]
    )
    supervisor = Supervisor(
        cast(ModelGateway, models),
        build_registry([]),
        checkpointer=InMemorySaver(),
        session_summary_service=FakeSessionSummaryService(),  # type: ignore[arg-type]
    )
    identity = AgentIdentity(
        subject="student-summary",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )

    first = await supervisor.invoke(
        SupervisorRequest(
            "我想改善体能", request().gateway_context, "conversation-1", identity=identity
        )
    )
    second = await supervisor.invoke(
        SupervisorRequest(
            "继续给我建议", request().gateway_context, "conversation-1", identity=identity
        )
    )

    assert first.answer == "第一轮回答。"
    assert second.answer == "第二轮回答。"
    second_round_messages = models.messages_seen[1]
    assert any("当前会话短期摘要" in str(message) for message in second_round_messages)
    assert any(message.get("content") == "我想改善体能" for message in second_round_messages)


async def test_supervisor_passes_unconfirmed_memory_candidate_to_model_context() -> None:
    models = FakeModels([ModelTurn(content="请确认是否保存这个偏好。", tool_calls=())])
    identity = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    supervisor = Supervisor(
        cast(ModelGateway, models),
        build_registry([]),
        memory_candidate_extractor=FakeCandidateExtractor(),  # type: ignore[arg-type]
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="请记住我喜欢弹力带",
            gateway_context=GatewayRequestContext(signed_context="signed-context"),
            conversation_id="conversation-memory-1",
            identity=identity,
        )
    )

    assert response.answer == "请确认是否保存这个偏好。"
    assert any(
        message["role"] == "system" and "不是已保存事实" in message["content"]
        for message in models.messages_seen[0]
    )


async def test_supervisor_restores_previous_conversation_messages() -> None:
    models = FakeModels(
        [
            ModelTurn(content="第一轮回答。", tool_calls=()),
            ModelTurn(content="第二轮回答。", tool_calls=()),
        ]
    )
    supervisor = Supervisor(
        cast(ModelGateway, models),
        build_registry([]),
        checkpointer=InMemorySaver(),
    )

    await supervisor.invoke(request("我想减脂"))
    response = await supervisor.invoke(request("继续安排训练"))

    assert response.answer == "第二轮回答。"
    assert models.tools_seen[1]
    # FakeModels 的工具参数之外没有记录消息，因此通过 Runtime 图状态验证历史。
    state = await supervisor._graph.aget_state({"configurable": {"thread_id": "conversation-1"}})
    contents = [message["content"] for message in state.values["messages"]]
    assert contents.count("我想减脂") == 1
    assert contents.count("继续安排训练") == 1


async def test_supervisor_rejects_legacy_checkpoint_with_sensitive_request() -> None:
    models = FakeModels([ModelTurn(content="不应执行", tool_calls=())])
    checkpointer = LegacyCheckpointSaver()
    supervisor = Supervisor(
        cast(ModelGateway, models),
        build_registry([]),
        checkpointer=checkpointer,
    )

    with pytest.raises(SupervisorCheckpointIncompatible):
        await supervisor.invoke(request("继续安排训练"))

    assert models.tools_seen == []


class LegacyCheckpointSaver(InMemorySaver):
    """只返回旧版敏感 State 的最小 Checkpoint 测试桩。"""

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple:
        checkpoint = empty_checkpoint()
        checkpoint["v"] = 4
        checkpoint["channel_values"] = {
            "request": {
                "user_message": "历史请求",
                "gateway_context": {"signed_context": "secret-context"},
            }
        }
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata={"step": -1},
            parent_config=None,
            pending_writes=[],
        )


async def test_supervisor_wraps_unknown_model_tool_as_runtime_failure() -> None:
    models = FakeModels(
        [
            ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-unknown",
                        name="fitness.unknown.v1",
                        arguments={},
                    ),
                ),
            )
        ]
    )
    supervisor = Supervisor(cast(ModelGateway, models), build_registry([]))

    with pytest.raises(SupervisorRuntimeError):
        await supervisor.invoke(request())


async def test_supervisor_rejects_legacy_business_scope_before_model_call() -> None:
    models = FakeModels([])
    supervisor = Supervisor(cast(ModelGateway, models), build_registry([]))

    with pytest.raises(UnsupportedLegacyRequest):
        await supervisor.invoke(request("帮我报名赛事活动"))

    assert models.tools_seen == []


def test_supervisor_route_guard_covers_fitness_business_boundaries() -> None:
    assert classify_route("帮我安排下周课程预约") == "BOOKING"
    assert classify_route("查看本月经营报表") == "OPERATIONS"
    assert classify_route("查看本月课程预约量") == "OPERATIONS"
    assert classify_route("我想制定减脂训练计划") == "FITNESS_COACHING"
    assert classify_route("查询比赛报名") == "UNSUPPORTED_LEGACY"
