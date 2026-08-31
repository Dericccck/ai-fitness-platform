"""验证 Supervisor + 领域子图拓扑和跨领域工具隔离。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.domain_subgraphs import domain_agent_spec, executable_domain_specs
from app.agent.supervisor import Supervisor, SupervisorRequest, SupervisorRuntimeError
from app.agent.tool_registry import ToolContext, ToolDefinition, ToolRegistry
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordingModel:
    """记录每个领域实际暴露给模型的工具集合。"""

    def __init__(self, tool_call: ModelToolCall | None = None) -> None:
        self.tool_call = tool_call
        self.tools_seen: list[set[str]] = []
        self.messages_seen: list[list[dict[str, Any]]] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        force_tool_name: str | None = None,
    ) -> ModelTurn:
        del force_tool_name
        self.messages_seen.append(messages)
        self.tools_seen.append({tool["function"]["name"] for tool in tools})
        if self.tool_call is not None:
            call = self.tool_call
            self.tool_call = None
            return ModelTurn(content="", tool_calls=(call,))
        return ModelTurn(content="领域回答", tool_calls=())


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(_: BaseModel, __: ToolContext) -> dict[str, str]:
        return {"status": "ok"}

    for tool_id in (
        "fitness.course.list.v1",
        "fitness.training.plan.generate_draft.v1",
        "fitness.booking.create.v1",
        "fitness.operations.metric.query.v1",
        "fitness.support.ticket.create.v1",
    ):
        registry.register(
            ToolDefinition(
                tool_id=tool_id,
                description=f"测试工具 {tool_id}",
                input_model=EmptyInput,
                handler=handler,
                allowed_roles=frozenset({"STUDENT", "COACH", "ORGANIZATION_ADMIN"}),
                read_only=True,
                requires_confirmation=False,
            )
        )
    return registry


def build_request(message: str, conversation_id: str) -> SupervisorRequest:
    return SupervisorRequest(
        user_message=message,
        gateway_context=GatewayRequestContext(
            signed_context="signed-context",
            request_id=f"request-{conversation_id}",
            trace_id=f"trace-{conversation_id}",
        ),
        conversation_id=conversation_id,
    )


def test_supervisor_contains_four_real_domain_subgraphs() -> None:
    supervisor = Supervisor(cast(ModelGateway, RecordingModel()), build_registry())

    assert set(supervisor.domain_graphs) == {
        "FITNESS_COACHING",
        "BOOKING",
        "OPERATIONS",
        "CUSTOMER_SERVICE",
    }
    top_level_nodes = set(supervisor._graph.get_graph().nodes)
    assert {
        "supervisor_router",
        "fitness_agent",
        "booking_agent",
        "operations_agent",
        "customer_service_agent",
    }.issubset(top_level_nodes)
    for graph in supervisor.domain_graphs.values():
        assert {"enter", "model", "tools", "confirmation"}.issubset(set(graph.get_graph().nodes))


@pytest.mark.parametrize(
    ("message", "route", "visible", "hidden"),
    (
        (
            "我想制定减脂训练计划",
            "FITNESS_COACHING",
            "fitness_training_plan_generate_draft_v1",
            "fitness_booking_create_v1",
        ),
        (
            "查询明天可约时间",
            "BOOKING",
            "fitness_booking_create_v1",
            "fitness_training_plan_generate_draft_v1",
        ),
        (
            "查看本月营收金额",
            "OPERATIONS",
            "fitness_operations_metric_query_v1",
            "fitness_course_list_v1",
        ),
        (
            "查询我的客服工单",
            "CUSTOMER_SERVICE",
            "fitness_support_ticket_create_v1",
            "fitness_operations_metric_query_v1",
        ),
    ),
)
async def test_domain_subgraph_exposes_only_its_tool_allowlist(
    message: str, route: str, visible: str, hidden: str
) -> None:
    model = RecordingModel()
    supervisor = Supervisor(cast(ModelGateway, model), build_registry())

    response = await supervisor.invoke(build_request(message, route.lower()))

    assert response.route == route
    assert visible in model.tools_seen[0]
    assert hidden not in model.tools_seen[0]


async def test_domain_subgraph_rejects_noncompliant_cross_domain_tool_call() -> None:
    model = RecordingModel(
        ModelToolCall(
            call_id="cross-domain-call",
            name="fitness.training.plan.generate_draft.v1",
            arguments={},
        )
    )
    supervisor = Supervisor(cast(ModelGateway, model), build_registry())

    with pytest.raises(SupervisorRuntimeError, match="白名单之外"):
        await supervisor.invoke(build_request("查询明天可约时间", "booking-cross-domain"))


def test_domain_specs_keep_high_risk_capabilities_separated() -> None:
    specs = {spec.route: spec for spec in executable_domain_specs()}

    assert "fitness.booking.create.v1" not in specs["FITNESS_COACHING"].allowed_tool_ids
    assert "fitness.training.plan.create_draft.v1" not in specs["BOOKING"].allowed_tool_ids
    assert specs["OPERATIONS"].allowed_tool_ids == frozenset({"fitness.operations.metric.query.v1"})
    assert "fitness.memory.save.v1" not in specs["CUSTOMER_SERVICE"].allowed_tool_ids
    assert domain_agent_spec("FITNESS_COACHING").supports_generated_training_draft is True


async def test_same_thread_can_switch_domain_without_reusing_old_system_context() -> None:
    """同一会话允许换领域，但新子图不能继承上一领域的临时系统提示。"""

    from langgraph.checkpoint.memory import InMemorySaver

    model = RecordingModel()
    supervisor = Supervisor(
        cast(ModelGateway, model),
        build_registry(),
        checkpointer=InMemorySaver(),
    )

    await supervisor.invoke(build_request("我想制定减脂训练计划", "switch-domain"))
    response = await supervisor.invoke(build_request("查询明天可约时间", "switch-domain"))

    assert response.route == "BOOKING"
    second_system_messages = [
        message["content"] for message in model.messages_seen[1] if message.get("role") == "system"
    ]
    assert len(second_system_messages) == 1
    assert "预约 Agent" in second_system_messages[0]
    assert "健身 Agent" not in second_system_messages[0]
    assert "fitness_booking_create_v1" in model.tools_seen[1]
    assert "fitness_training_plan_generate_draft_v1" not in model.tools_seen[1]
