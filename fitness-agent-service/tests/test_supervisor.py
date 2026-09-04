from typing import Any, cast

import pytest
from langgraph.checkpoint.base import CheckpointTuple, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from app.agent.supervisor import (
    Supervisor,
    SupervisorCheckpointIncompatible,
    SupervisorRequest,
    SupervisorRuntimeError,
    UnsupportedLegacyRequest,
    _forced_write_tool_name,
    _is_explicit_training_plan_creation_request,
    _model_tools,
    _system_prompt,
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
        self.force_tool_names: list[str | None] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        force_tool_name: str | None = None,
    ) -> ModelTurn:
        self.tools_seen.append(tools)
        self.messages_seen.append(messages)
        self.force_tool_names.append(force_tool_name)
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


class FakeRagResult:
    def as_prompt_context(self) -> str:
        return "知识引用：预约取消规则需要在开课前完成。"


class FakeRagService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, _: Any) -> FakeRagResult:
        self.queries.append(query)
        return FakeRagResult()


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
            tool_id="fitness.course.list.v1",
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
                        name="fitness.course.list.v1",
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
    assert response.route == "CUSTOMER_SERVICE"
    assert response.tool_steps == 1
    assert response.input_tokens == 18
    assert response.output_tokens == 10
    assert calls == [{"value": "课程", "signed_context": "signed-context"}]
    # 达到预算后第二回合不再暴露任何工具，只允许模型总结真实结果。
    assert len(models.tools_seen) == 2
    assert models.tools_seen[0]
    assert models.tools_seen[1] == []
    assert models.force_tool_names == [None, None]


async def test_supervisor_forces_booking_create_tool_for_explicit_create_request() -> None:
    assert (
        _forced_write_tool_name("BOOKING", "请创建预约：明天九点的瑜伽课")
        == "fitness_booking_create_v1"
    )
    assert _forced_write_tool_name("BOOKING", "查询明天可约时间") is None
    assert _forced_write_tool_name("BOOKING", "不要创建、改约或取消预约，只查询课程") is None
    assert (
        _forced_write_tool_name("BOOKING", "不要查询，帮我取消预约") == "fitness_booking_cancel_v1"
    )


def test_supervisor_forces_customer_service_ticket_for_explicit_submit_request() -> None:
    assert (
        _forced_write_tool_name("CUSTOMER_SERVICE", "请帮我提交客服工单，反馈预约状态异常")
        == "fitness_support_ticket_create_v1"
    )
    assert _forced_write_tool_name("CUSTOMER_SERVICE", "查询我的客服工单") is None
    assert _forced_write_tool_name("CUSTOMER_SERVICE", "不要提交客服工单，只查询记录") is None


def test_training_plan_creation_requires_affirmative_intent() -> None:
    assert _is_explicit_training_plan_creation_request("请帮我生成训练计划") is True
    assert _is_explicit_training_plan_creation_request("不要生成训练计划，只给普通建议") is False


def test_model_tool_schema_hides_context_bound_organization_id() -> None:
    class OrganizationQueryInput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        organization_id: str
        limit: int = 20

    registry = ToolRegistry()

    async def handler(_: BaseModel, __: ToolContext) -> dict[str, str]:
        return {"status": "ok"}

    registry.register(
        ToolDefinition(
            tool_id="fitness.course.list.v1",
            description="查询机构课程",
            input_model=OrganizationQueryInput,
            handler=handler,
            allowed_roles=frozenset({"ORGANIZATION_ADMIN"}),
            read_only=True,
            requires_confirmation=False,
        )
    )

    schemas = _model_tools(registry, "BOOKING")

    parameters = schemas[0]["function"]["parameters"]
    assert "organization_id" not in parameters["properties"]
    assert "organization_id" not in parameters.get("required", [])
    assert "limit" in parameters["properties"]


def test_model_tool_schema_removes_non_constraint_metadata() -> None:
    class Input(BaseModel):
        count: int = Field(default=5, examples=[3], ge=1, description="返回数量")

    registry = ToolRegistry()

    async def handler(_: BaseModel, __: ToolContext) -> object:
        return {}

    registry.register(
        ToolDefinition(
            tool_id="fitness.booking.availability.check.v1",
            description="测试工具 Schema 压缩",
            input_model=Input,
            handler=handler,
            allowed_roles=frozenset({"STUDENT"}),
            read_only=True,
            requires_confirmation=False,
        )
    )

    parameters = _model_tools(registry, "BOOKING")[0]["function"]["parameters"]

    assert "title" not in parameters
    assert "title" not in parameters["properties"]["count"]
    assert "default" not in parameters["properties"]["count"]
    assert "examples" not in parameters["properties"]["count"]
    assert parameters["properties"]["count"]["minimum"] == 1
    assert parameters["properties"]["count"]["description"] == "返回数量"


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
    assert classify_route("我的预约是什么时间") == "CUSTOMER_SERVICE"
    assert classify_route("我的合同还有多少课时") == "CUSTOMER_SERVICE"
    assert classify_route("请告诉我预约规则") == "CUSTOMER_SERVICE"
    assert classify_route("请提交客服工单，反馈预约状态异常") == "CUSTOMER_SERVICE"
    assert classify_route("查询明天可约时间") == "BOOKING"
    assert classify_route("查看本月经营报表") == "OPERATIONS"
    assert classify_route("查看本月课程预约量") == "OPERATIONS"
    assert classify_route("查看本月完课量") == "OPERATIONS"
    assert classify_route("查看本月新客量") == "OPERATIONS"
    assert classify_route("查看本月营收金额") == "OPERATIONS"
    assert classify_route("查看本月教练预约量") == "OPERATIONS"
    assert classify_route("我想制定减脂训练计划") == "FITNESS_COACHING"
    assert classify_route("查询比赛报名") == "UNSUPPORTED_LEGACY"


async def test_customer_service_only_exposes_read_tools() -> None:
    models = FakeModels([ModelTurn(content="已查询到你的预约信息。", tool_calls=())])
    supervisor = Supervisor(cast(ModelGateway, models), build_registry([]))

    response = await supervisor.invoke(request("我的预约是什么时间"))

    assert response.route == "CUSTOMER_SERVICE"
    assert models.tools_seen[0]
    assert all(
        tool["function"]["name"] == "fitness_course_list_v1" for tool in models.tools_seen[0]
    )
    assert all(
        "create" not in tool["function"]["name"]
        and "cancel" not in tool["function"]["name"]
        and "publish" not in tool["function"]["name"]
        for tool in models.tools_seen[0]
    )


async def test_customer_service_rejects_medical_request_before_model_call() -> None:
    models = FakeModels([])
    supervisor = Supervisor(cast(ModelGateway, models), build_registry([]))

    response = await supervisor.invoke(request("我的膝盖疼痛应该吃什么药"))

    assert response.route == "CUSTOMER_SERVICE"
    assert response.tool_steps == 0
    assert "不提供诊断、用药或治疗建议" in response.answer
    assert models.tools_seen == []


async def test_customer_service_knowledge_question_uses_rag_but_business_query_does_not() -> None:
    rag = FakeRagService()
    identity = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    models = FakeModels(
        [
            ModelTurn(content="规则是开课前取消。", tool_calls=()),
            ModelTurn(content="你的预约在明天九点。", tool_calls=()),
        ]
    )
    supervisor = Supervisor(cast(ModelGateway, models), build_registry([]), rag_service=rag)  # type: ignore[arg-type]

    await supervisor.invoke(
        SupervisorRequest(
            user_message="请告诉我预约取消规则",
            gateway_context=GatewayRequestContext(signed_context="signed-context"),
            conversation_id="conversation-customer-service-1",
            identity=identity,
        )
    )
    await supervisor.invoke(
        SupervisorRequest(
            user_message="我的预约是什么时间",
            gateway_context=GatewayRequestContext(signed_context="signed-context"),
            conversation_id="conversation-customer-service-2",
            identity=identity,
        )
    )

    assert rag.queries == ["请告诉我预约取消规则"]
    assert "知识引用：预约取消规则需要在开课前完成。" in str(models.messages_seen[0])


def test_customer_service_prompt_declares_confirmation_and_no_fake_ticket() -> None:
    prompt = _system_prompt("CUSTOMER_SERVICE", "zh-CN")

    assert "只能通过只读工具查询" in prompt
    assert "尚未创建工单" in prompt
    assert "不提供诊断、用药或治疗建议" in prompt


def test_booking_prompt_requires_write_tool_and_confirmation_flow() -> None:
    prompt = _system_prompt("BOOKING", "zh-CN")

    assert "必须调用对应的预约工具" in prompt
    assert "自动生成确认单并暂停" in prompt
