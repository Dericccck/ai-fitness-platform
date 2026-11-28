"""Supervisor 下属领域 Agent 子图的定义与装配。

这里把“多 Agent”落实为真正的 LangGraph 子图，并按领域复杂度采用局部异构拓扑：

* 顶层 Supervisor 只负责意图路由和子图调度；
* 每个领域 Agent 都拥有独立的图节点命名空间和固定工具白名单；
* 经营分析 Agent 使用双模式专属子图：常规看数走确定性/受控 Query DSL 单向流水线
  （enter -> classify_mode -> plan_direct -> execute_queries -> summarize），
  经营诊断与归因走受控只读 ReAct 下钻循环（diagnose_step <-> execute_diagnosis_queries
  -> synthesize_diagnosis），全程无写操作确认节点；
* 健身指导 Agent 使用专属的定制子图（enter -> prepare_memory -> model <-> tools ->
  verify_citations / confirmation），将记忆候选提取、知识引用强校验与训练草案直转确认
  显式收敛到领域图拓扑内；
* 预约与客服领域复用同一套受控 ReAct + HITL 安全内核（model/tools/confirmation），
  避免四份风控逻辑逐渐漂移；
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
            raise ValueError(f"领域子图路由不匹配：期望={spec.route}，实际={route}")
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


def build_operations_subgraph(
    spec: DomainAgentSpec,
    *,
    state_schema: Any,
    context_schema: Any,
    classify_mode_node: Callable[..., Any],
    after_classify_mode: Callable[..., str],
    plan_direct_node: Callable[..., Any],
    execute_direct_node: Callable[..., Any],
    summarize_direct_node: Callable[..., Any],
    diagnose_step_node: Callable[..., Any],
    after_diagnose_step: Callable[..., str],
    execute_diagnosis_node: Callable[..., Any],
    synthesize_diagnosis_node: Callable[..., Any],
) -> Any:
    """编译经营分析领域专属的双模式异构子图（直接查数流水线 + 受控只读诊断 ReAct）。

    拓扑完全只读，不挂载写操作 ``confirmation`` 节点，也不向模型暴露任意 SQL 工具：

    1. 常规看数分支（单向流水线，无循环）：
       ``START -> enter -> classify_mode -> plan_direct -> execute_queries -> summarize -> END``
       标准问法走规则快路径（0 次前置 LLM）；口语或复合多指标走一次受控 JSON Query DSL 填槽，
       随后并发查询固定指标并流式总结。
    2. 经营诊断与归因分支（受控只读 ReAct 有限循环）：
       ``START -> enter -> classify_mode -> diagnose_step``
       ``<-> execute_diagnosis_queries -> synthesize_diagnosis -> END``
       结合指标拆解树与程序计算的贡献度占比，在硬步数与总查数上限内按需下钻，最终按三段式
       防幻觉结构输出诊断报告。
    """

    graph = StateGraph(state_schema, context_schema=context_schema)

    def enter_domain(state: Any) -> dict[str, Any]:
        route = state.get("route")
        if route != spec.route:
            raise ValueError(f"领域子图路由不匹配：期望={spec.route}，实际={route}")
        return {"active_domain": spec.node_name}

    graph.add_node("enter", enter_domain)
    graph.add_node("classify_mode", classify_mode_node)
    graph.add_node("plan_direct", plan_direct_node)
    graph.add_node("execute_queries", execute_direct_node)
    graph.add_node("summarize", summarize_direct_node)
    graph.add_node("diagnose_step", diagnose_step_node)
    graph.add_node("execute_diagnosis_queries", execute_diagnosis_node)
    graph.add_node("synthesize_diagnosis", synthesize_diagnosis_node)

    graph.add_edge(START, "enter")
    graph.add_edge("enter", "classify_mode")
    graph.add_conditional_edges(
        "classify_mode",
        after_classify_mode,
        {"direct": "plan_direct", "diagnosis": "diagnose_step"},
    )
    graph.add_edge("plan_direct", "execute_queries")
    graph.add_edge("execute_queries", "summarize")
    graph.add_edge("summarize", END)

    graph.add_conditional_edges(
        "diagnose_step",
        after_diagnose_step,
        {"query": "execute_diagnosis_queries", "synthesize": "synthesize_diagnosis"},
    )
    graph.add_edge("execute_diagnosis_queries", "diagnose_step")
    graph.add_edge("synthesize_diagnosis", END)
    return graph.compile()


def build_fitness_subgraph(
    spec: DomainAgentSpec,
    *,
    state_schema: Any,
    context_schema: Any,
    prepare_memory_node: Callable[..., Any],
    model_node: Callable[..., Any],
    tool_node: Callable[..., Any],
    verify_citations_node: Callable[..., Any],
    confirmation_node: Callable[..., Any],
    after_model: Callable[..., str],
    after_tools: Callable[..., str],
) -> Any:
    """编译健身指导与训练计划领域专属的异构子图。

    健身领域同时承载 Memory 候选提取、知识问答 ``[证据N]`` 引用强校验以及训练草案生成后
    直转写确认单等专属逻辑。将这些步骤显式建模为子图节点，避免污染通用 ReAct 节点，同时
    继续复用父图 Checkpointer 与统一 ``confirmation`` 安全内核：

    ``START -> enter -> prepare_memory -> model``
      - ``tools`` -> ``tools`` -> (``confirmation`` | ``model``)
      - ``verify`` -> ``verify_citations`` -> ``END``
      - ``confirmation`` -> ``confirmation`` -> ``END``
      - ``finish`` -> ``END``
    """

    graph = StateGraph(state_schema, context_schema=context_schema)

    def enter_domain(state: Any) -> dict[str, Any]:
        route = state.get("route")
        if route != spec.route:
            raise ValueError(f"领域子图路由不匹配：期望={spec.route}，实际={route}")
        return {"active_domain": spec.node_name}

    graph.add_node("enter", enter_domain)
    graph.add_node("prepare_memory", prepare_memory_node)
    graph.add_node("model", model_node)
    graph.add_node("tools", tool_node)
    graph.add_node("verify_citations", verify_citations_node)
    graph.add_node("confirmation", confirmation_node)

    graph.add_edge(START, "enter")
    graph.add_edge("enter", "prepare_memory")
    graph.add_edge("prepare_memory", "model")
    graph.add_conditional_edges(
        "model",
        after_model,
        {
            "tools": "tools",
            "verify": "verify_citations",
            "confirmation": "confirmation",
            "finish": END,
        },
    )
    graph.add_conditional_edges(
        "tools",
        after_tools,
        {"confirmation": "confirmation", "model": "model"},
    )
    graph.add_edge("verify_citations", END)
    graph.add_edge("confirmation", END)
    return graph.compile()
