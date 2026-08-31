"""Supervisor 下属领域 Agent 子图的定义与装配。

这里把“多 Agent”落实为真正的 LangGraph 子图，而不是只在 Prompt 中声明角色：

* 顶层 Supervisor 只负责意图路由和子图调度；
* 每个领域 Agent 都拥有独立的图节点命名空间和固定工具白名单；
* 模型、工具执行和确认节点复用同一套安全实现，避免四份逻辑逐渐漂移；
* 子图不创建自己的 Checkpointer，统一继承父图的 PostgreSQL Checkpoint，保证一次
  会话、一次确认和一次恢复只存在一个事实来源。

领域子图仍运行在同一个 Python Agent Service 中。未来只有在流量、团队所有权或故障域
确实需要独立扩缩容时，才考虑拆成多个部署服务。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

SupervisorRoute = Literal[
    "FITNESS_COACHING",
    "BOOKING",
    "OPERATIONS",
    "CUSTOMER_SERVICE",
    "UNSUPPORTED_LEGACY",
]


@dataclass(frozen=True)
class DomainAgentSpec:
    """一个领域 Agent 的不可变能力边界。"""

    route: SupervisorRoute
    node_name: str
    display_name: str
    allowed_tool_ids: frozenset[str]
    uses_rag: bool = False
    uses_memory_candidates: bool = False
    deterministic_operations_input: bool = False
    supports_generated_training_draft: bool = False


# 工具白名单是“模型可见能力”边界。ToolRegistry 和 Java Gateway 仍会继续执行角色、
# 机构、资源归属与确认凭证校验；这里负责阻止模型跨领域尝试无关工具。
DOMAIN_AGENT_SPECS: Mapping[SupervisorRoute, DomainAgentSpec] = {
    "FITNESS_COACHING": DomainAgentSpec(
        route="FITNESS_COACHING",
        node_name="fitness_agent",
        display_name="健身指导 Agent",
        allowed_tool_ids=frozenset(
            {
                "fitness.user.get_current.v1",
                "fitness.organization.get.v1",
                "fitness.course.list.v1",
                "fitness.contract.list.v1",
                "fitness.appointment.list.v1",
                "fitness.training.plan.get.v1",
                "fitness.training.plan.generate_draft.v1",
                "fitness.training.plan.create_draft.v1",
                "fitness.training.plan.submit_review.v1",
                "fitness.training.plan.review.v1",
                "fitness.training.plan.publish.v1",
                "fitness.training.day.executions.list.v1",
                "fitness.training.day.record_execution.v1",
                "fitness.memory.list.v1",
                "fitness.memory.save.v1",
                "fitness.memory.revoke.v1",
            }
        ),
        uses_rag=True,
        uses_memory_candidates=True,
        supports_generated_training_draft=True,
    ),
    "BOOKING": DomainAgentSpec(
        route="BOOKING",
        node_name="booking_agent",
        display_name="预约 Agent",
        allowed_tool_ids=frozenset(
            {
                "fitness.user.get_current.v1",
                "fitness.organization.get.v1",
                "fitness.course.list.v1",
                "fitness.contract.list.v1",
                "fitness.appointment.list.v1",
                "fitness.booking.availability.check.v1",
                "fitness.booking.create.v1",
                "fitness.booking.reschedule.v1",
                "fitness.booking.cancel.v1",
            }
        ),
    ),
    "OPERATIONS": DomainAgentSpec(
        route="OPERATIONS",
        node_name="operations_agent",
        display_name="经营分析 Agent",
        allowed_tool_ids=frozenset({"fitness.operations.metric.query.v1"}),
        deterministic_operations_input=True,
    ),
    "CUSTOMER_SERVICE": DomainAgentSpec(
        route="CUSTOMER_SERVICE",
        node_name="customer_service_agent",
        display_name="客服 Agent",
        allowed_tool_ids=frozenset(
            {
                "fitness.user.get_current.v1",
                "fitness.organization.get.v1",
                "fitness.course.list.v1",
                "fitness.contract.list.v1",
                "fitness.appointment.list.v1",
                "fitness.booking.availability.check.v1",
                "fitness.training.plan.get.v1",
                "fitness.training.day.executions.list.v1",
                "fitness.support.ticket.list.v1",
                "fitness.support.ticket.get.v1",
                "fitness.support.ticket.create.v1",
            }
        ),
        uses_rag=True,
    ),
}

EXECUTABLE_DOMAIN_ROUTES: tuple[SupervisorRoute, ...] = (
    "FITNESS_COACHING",
    "BOOKING",
    "OPERATIONS",
    "CUSTOMER_SERVICE",
)


def domain_agent_spec(route: SupervisorRoute) -> DomainAgentSpec:
    """返回可执行领域规格；旧模块路由永远不能进入领域子图。"""

    try:
        return DOMAIN_AGENT_SPECS[route]
    except KeyError as exc:
        raise ValueError(f"路由没有可执行的领域子图：{route}") from exc


def executable_domain_specs() -> tuple[DomainAgentSpec, ...]:
    """按稳定顺序返回四个领域 Agent，便于图装配、测试和能力目录展示。"""

    return tuple(DOMAIN_AGENT_SPECS[route] for route in EXECUTABLE_DOMAIN_ROUTES)


def build_domain_subgraph(
    spec: DomainAgentSpec,
    *,
    state_schema: Any,
    context_schema: Any,
    model_node: Callable[..., Any],
    tool_node: Callable[..., Any],
    confirmation_node: Callable[..., Any],
    after_model: Callable[..., str],
) -> Any:
    """编译单个领域 Agent 子图。

    子图故意不传 ``checkpointer``：LangGraph 会让它继承父图的持久化上下文。若子图
    自己再创建 Checkpointer，同一确认中断会形成两套 thread/namespace，恢复时容易
    出现父图已完成、子图仍暂停的不一致。
    """

    graph = StateGraph(state_schema, context_schema=context_schema)

    def enter_domain(state: Any) -> dict[str, Any]:
        route = state.get("route")
        if route != spec.route:
            raise ValueError(
                f"领域子图路由不匹配：期望={spec.route}，实际={route}"
            )
        return {"active_domain": spec.node_name}

    graph.add_node("enter", enter_domain)
    graph.add_node("model", model_node)
    graph.add_node("tools", tool_node)
    graph.add_node("confirmation", confirmation_node)
    graph.add_edge(START, "enter")
    graph.add_edge("enter", "model")
    graph.add_conditional_edges(
        "model",
        after_model,
        {"tools": "tools", "confirmation": "confirmation", "finish": END},
    )
    graph.add_edge("tools", "model")
    graph.add_edge("confirmation", END)
    return graph.compile()
