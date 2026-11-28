"""经营 Agent 从自然语言到受控工具调用的端到端单元联调。"""

import json
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

from app.agent.operations_tools import build_operations_tool_definitions
from app.agent.supervisor import Supervisor, SupervisorRequest, SupervisorRuntimeError
from app.agent.tool_registry import ToolRegistry
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import (
    GatewayOperationsMetric,
    GatewayRequestContext,
)
from app.infrastructure.model_gateway import (
    JsonModelTurn,
    ModelGateway,
    ModelResponseError,
    ModelTurn,
)


class FakeOperationsModels:
    """模拟经营分析异构子图的总结模型：仅被调用一次且不暴露任何工具。"""

    def __init__(self, answer: str = "本月营收金额为 30000，按周呈上升趋势。") -> None:
        self.answer = answer
        self.turn_index = 0
        self.messages_seen: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        self.messages_seen.append(messages)
        self.tools_seen.append(tools)
        self.turn_index += 1
        assert tools == []
        return ModelTurn(
            content=self.answer,
            tool_calls=(),
            input_tokens=18,
            output_tokens=12,
        )


class FakeOperationsGateway:
    """返回固定聚合结果，模拟 Java Gateway 的稳定响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date | None, date | None, str]] = []

    async def query_operations_metric(
        self,
        _: GatewayRequestContext,
        organization_id: str,
        metric: str,
        *,
        from_date: date | None,
        to_date: date | None,
        limit: int,
        bucket: str,
    ) -> GatewayOperationsMetric:
        self.calls.append((organization_id, metric, from_date, to_date, bucket))
        assert limit == 20
        return GatewayOperationsMetric.model_validate(
            {
                "metric": metric,
                "bucket": bucket,
                "organizationId": organization_id,
                "from": from_date,
                "to": to_date,
                "rows": [
                    {"dimension": "2026-08-03", "label": "2026-08-03", "value": 12000},
                    {"dimension": "2026-08-10", "label": "2026-08-10", "value": 18000},
                ],
                "generatedAt": datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            }
        )


class RecordingOperationsAudit:
    """只记录审计元数据，测试确保不会保存 SQL 或模型原文。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, **event: Any) -> None:
        self.events.append(event)


async def test_operations_request_runs_route_tool_gateway_audit_and_final_answer() -> None:
    models = FakeOperationsModels()
    gateway = FakeOperationsGateway()
    audit = RecordingOperationsAudit()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(
        cast(Any, gateway),
        audit_repository=cast(Any, audit),
    ):
        registry.register(definition)
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        max_tool_steps=1,
    )
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="查看 2026-08-01 到 2026-08-15 的营收金额按周趋势",
            gateway_context=GatewayRequestContext(
                signed_context="signed-context",
                request_id="operations-request-1",
                trace_id="operations-trace-1",
            ),
            conversation_id="operations-conversation-1",
            thread_id="operations-thread-1",
            identity=identity,
        )
    )

    assert response.route == "OPERATIONS"
    assert response.tool_steps == 1
    assert response.answer == "本月营收金额为 30000，按周呈上升趋势。"
    assert models.turn_index == 1
    assert any("当前路由=OPERATIONS" in str(m.get("content")) for m in models.messages_seen[0])
    assert any("REVENUE_AMOUNT" in str(m.get("content")) for m in models.messages_seen[0])
    assert any("30000" in str(m.get("content")) for m in models.messages_seen[0])
    assert gateway.calls == [
        ("org-1", "REVENUE_AMOUNT", date(2026, 8, 1), date(2026, 8, 15), "WEEK")
    ]
    assert [
        (event["metric"], event["comparison_role"], event["status"]) for event in audit.events
    ] == [("REVENUE_AMOUNT", "CURRENT", "SUCCEEDED")]
    assert audit.events[0]["row_count"] == 2
    assert "sql" not in audit.events[0]
    assert "prompt" not in audit.events[0]


async def test_student_operations_request_is_rejected_before_gateway() -> None:
    """验证学员不能通过自然语言绕过经营查询的管理员工具权限，且不浪费模型调用。"""

    models = FakeOperationsModels()
    gateway = FakeOperationsGateway()
    audit = RecordingOperationsAudit()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(
        cast(Any, gateway),
        audit_repository=cast(Any, audit),
    ):
        registry.register(definition)
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        max_tool_steps=1,
    )
    identity = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )

    with pytest.raises(SupervisorRuntimeError, match="Supervisor 执行失败"):
        await supervisor.invoke(
            SupervisorRequest(
                user_message="查看 2026-08-01 到 2026-08-15 的营收金额按周趋势",
                gateway_context=GatewayRequestContext(
                    signed_context="signed-context",
                    request_id="student-operations-request-1",
                    trace_id="student-operations-trace-1",
                ),
                conversation_id="student-operations-conversation-1",
                thread_id="student-operations-thread-1",
                identity=identity,
            )
        )

    assert models.turn_index == 0
    assert gateway.calls == []
    assert audit.events == []


async def test_ambiguous_operations_question_skips_query_and_clarifies() -> None:
    """歧义经营问题不触发查数报错，而是跳过 query_metric 直接澄清。"""

    from app.agent.routing import RouteDecision, RoutedIntent

    class StaticOpsRouter:
        async def decide(self, *_: Any, **__: Any) -> RouteDecision:
            return RouteDecision(
                intents=(RoutedIntent("OPERATIONS", "QUERY_OPERATIONS_METRIC"),),
                source="LLM",
                confidence=0.92,
                reason_code="OPS_AMBIGUOUS",
                classifier_version="test",
            )

    models = FakeOperationsModels(answer="请明确你要查询的具体经营指标和时间范围。")
    gateway = FakeOperationsGateway()
    audit = RecordingOperationsAudit()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(
        cast(Any, gateway),
        audit_repository=cast(Any, audit),
    ):
        registry.register(definition)
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        intent_router=StaticOpsRouter(),
    )
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="查看本月经营情况",
            gateway_context=GatewayRequestContext(
                signed_context="signed-context",
                request_id="ops-ambiguous-1",
                trace_id="ops-ambiguous-trace-1",
            ),
            conversation_id="ops-ambiguous-conv",
            identity=identity,
        )
    )

    assert response.route == "OPERATIONS"
    assert response.tool_steps == 0
    assert response.answer == "请明确你要查询的具体经营指标和时间范围。"
    assert models.turn_index == 1
    assert any("先向用户澄清" in str(m.get("content")) for m in models.messages_seen[0])
    assert gateway.calls == []
    assert audit.events == []


async def test_multi_org_operations_request_fails_closed_before_model() -> None:
    """多机构上下文无法唯一选定门店时必须 fail-closed，且不触发模型或网关调用。"""

    models = FakeOperationsModels()
    gateway = FakeOperationsGateway()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(cast(Any, gateway)):
        registry.register(definition)
    supervisor = Supervisor(cast(ModelGateway, models), registry)
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1", "org-2"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    with pytest.raises(SupervisorRuntimeError, match="无法安全准备经营查询"):
        await supervisor.invoke(
            SupervisorRequest(
                user_message="查看 2026-08-01 到 2026-08-15 的营收金额按周趋势",
                gateway_context=GatewayRequestContext(
                    signed_context="signed-context",
                    request_id="ops-multi-org-1",
                    trace_id="ops-multi-org-trace-1",
                ),
                conversation_id="ops-multi-org-conv",
                identity=identity,
            )
        )

    assert models.turn_index == 0
    assert gateway.calls == []


class ScriptedOperationsPlannerModel:
    """支持受控 JSON 规划与流式总结的双接口测试模型。"""

    def __init__(
        self,
        json_responses: list[str | Exception],
        summary_answer: str,
    ) -> None:
        self.json_responses = list(json_responses)
        self.summary_answer = summary_answer
        self.json_calls = 0
        self.summary_calls = 0
        self.json_messages_seen: list[list[dict[str, str]]] = []
        self.summary_messages_seen: list[list[dict[str, Any]]] = []

    async def chat_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> JsonModelTurn:
        del max_output_tokens, temperature
        self.json_calls += 1
        self.json_messages_seen.append(messages)
        if not self.json_responses:
            raise AssertionError("unexpected extra chat_json_with_usage call")
        next_item = self.json_responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return JsonModelTurn(content=next_item, input_tokens=30, output_tokens=20)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        assert tools == []
        self.summary_calls += 1
        self.summary_messages_seen.append(messages)
        return ModelTurn(
            content=self.summary_answer,
            tool_calls=(),
            input_tokens=40,
            output_tokens=25,
        )


class RichOperationsGateway:
    """按指标与时间窗口返回确定性聚合行，用于验证多指标并发与归因贡献度计算。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date | None, date | None, str]] = []

    async def query_operations_metric(
        self,
        _: GatewayRequestContext,
        organization_id: str,
        metric: str,
        *,
        from_date: date | None,
        to_date: date | None,
        limit: int,
        bucket: str,
    ) -> GatewayOperationsMetric:
        del limit
        self.calls.append((organization_id, metric, from_date, to_date, bucket))
        is_current = from_date is not None and from_date >= date(2026, 8, 1)
        rows: list[dict[str, Any]]
        if metric == "REVENUE_AMOUNT":
            rows = [{"dimension": "total", "label": "总计", "value": 42000 if is_current else 50000}]
        elif metric == "COMPLETED_CLASS_COUNT":
            rows = [{"dimension": "total", "label": "总计", "value": 60 if is_current else 100}]
        elif metric == "COACH_APPOINTMENT_COUNT":
            rows = (
                [
                    {"dimension": "coach-wang", "label": "王教练", "value": 12},
                    {"dimension": "coach-li", "label": "李教练", "value": 18},
                ]
                if is_current
                else [
                    {"dimension": "coach-wang", "label": "王教练", "value": 40},
                    {"dimension": "coach-li", "label": "李教练", "value": 30},
                ]
            )
        elif metric == "APPOINTMENT_STATUS_BREAKDOWN":
            rows = [
                {"dimension": "COMPLETED", "label": "已完成", "value": 60},
                {"dimension": "CANCELLED", "label": "已取消", "value": 25},
            ]
        elif metric == "NEW_CUSTOMER_COUNT":
            rows = [{"dimension": "total", "label": "总计", "value": 15 if is_current else 16}]
        else:
            rows = [{"dimension": "total", "label": "总计", "value": 80 if is_current else 100}]
        return GatewayOperationsMetric.model_validate(
            {
                "metric": metric,
                "bucket": bucket,
                "organizationId": organization_id,
                "from": from_date or date(2026, 8, 1),
                "to": to_date or date(2026, 8, 15),
                "rows": rows,
                "generatedAt": datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            }
        )


async def test_colloquial_multi_metric_direct_query_uses_llm_dsl_and_executes_concurrently() -> (
    None
):
    """口语化多指标问法走受控 JSON 填槽，单轮并发查数并流式总结（非 ReAct 循环）。"""

    from app.agent.routing import RouteDecision, RoutedIntent

    class StaticOpsRouter:
        async def decide(self, *_: Any, **__: Any) -> RouteDecision:
            return RouteDecision(
                intents=(RoutedIntent("OPERATIONS", "QUERY_OPERATIONS_METRIC"),),
                source="LLM",
                confidence=0.95,
                reason_code="OPS_MULTI_METRIC",
                classifier_version="test",
            )

    dsl_json = (
        '{"queries":['
        '{"metric":"REVENUE_AMOUNT","from":"2026-08-01","to":"2026-08-14","bucket":"NONE","comparison":"NONE","limit":20},'
        '{"metric":"COMPLETED_CLASS_COUNT","from":"2026-08-01","to":"2026-08-14","bucket":"NONE","comparison":"NONE","limit":20}'
        '],"needs_clarification":false,"clarification_reason":"","confidence":0.95}'
    )
    models = ScriptedOperationsPlannerModel(
        [dsl_json],
        summary_answer="近两周营收 42000，完课量 60 节。",
    )
    gateway = RichOperationsGateway()
    audit = RecordingOperationsAudit()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(
        cast(Any, gateway), audit_repository=cast(Any, audit)
    ):
        registry.register(definition)
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        intent_router=StaticOpsRouter(),
    )
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="近两周卖课回款和消课怎么样？",
            gateway_context=GatewayRequestContext("signed", "req-dsl-1", "trace-dsl-1"),
            conversation_id="ops-dsl-conv",
            identity=identity,
        )
    )

    assert response.route == "OPERATIONS"
    assert response.tool_steps == 2
    assert response.answer == "近两周营收 42000，完课量 60 节。"
    assert models.json_calls == 1
    assert models.summary_calls == 1
    assert {call[1] for call in gateway.calls} == {"REVENUE_AMOUNT", "COMPLETED_CLASS_COUNT"}
    assert all(call[0] == "org-1" for call in gateway.calls)
    assert len(audit.events) == 2
    summary_text = json.dumps(models.summary_messages_seen[0], ensure_ascii=False)
    assert "42000" in summary_text and "60" in summary_text
    assert "先向用户澄清" not in summary_text


async def test_diagnosis_react_loop_drills_down_and_computes_dimension_contributions() -> None:
    """经营诊断走受控只读 ReAct 循环：按拆解树多步下钻，由 Python 算教练波动贡献度。"""

    step1 = (
        '{"action":"QUERY","rationale":"先查本月完课量环比确认跌幅",'
        '"queries":[{"metric":"COMPLETED_CLASS_COUNT","from":"2026-08-01","to":"2026-08-15",'
        '"bucket":"NONE","comparison":"PREVIOUS_PERIOD","limit":20}]}'
    )
    step2 = (
        '{"action":"QUERY","rationale":"完课量下降 40 节，继续下钻教练维度与预约状态分布",'
        '"queries":['
        '{"metric":"COACH_APPOINTMENT_COUNT","from":"2026-08-01","to":"2026-08-15","bucket":"NONE","comparison":"PREVIOUS_PERIOD","limit":20},'
        '{"metric":"APPOINTMENT_STATUS_BREAKDOWN","from":"2026-08-01","to":"2026-08-15","bucket":"NONE","comparison":"NONE","limit":20}'
        "]}"
    )
    step3 = '{"action":"SYNTHESIZE","rationale":"已定位主要波动维度，进入三段式诊断总结","queries":[]}'
    models = ScriptedOperationsPlannerModel(
        [step1, step2, step3],
        summary_answer="已证实完课量环比减少 40 节；教练维度拆解显示王教练贡献 70% 降幅，建议核查排期。",
    )
    gateway = RichOperationsGateway()
    audit = RecordingOperationsAudit()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(
        cast(Any, gateway), audit_repository=cast(Any, audit)
    ):
        registry.register(definition)
    supervisor = Supervisor(cast(ModelGateway, models), registry)
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="本月完课量为什么下降了？帮我排查原因",
            gateway_context=GatewayRequestContext("signed", "req-diag-1", "trace-diag-1"),
            conversation_id="ops-diag-conv",
            identity=identity,
        )
    )

    assert response.route == "OPERATIONS"
    assert response.tool_steps == 3
    assert models.json_calls == 3
    assert models.summary_calls == 1
    assert {call[1] for call in gateway.calls} == {
        "COMPLETED_CLASS_COUNT",
        "COACH_APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
    }
    synthesis_payload = str(models.summary_messages_seen[0][-1]["content"])
    assert '"causality_status": "NOT_ESTABLISHED"' in synthesis_payload
    assert '"label": "王教练"' in synthesis_payload
    assert '"contribution_share_percent": 70.0' in synthesis_payload
    assert '"delta": -28' in synthesis_payload


async def test_diagnosis_falls_back_to_deterministic_tree_when_planner_llm_fails() -> None:
    """规划 LLM 异常时，经营诊断降级到确定性拆解树，仍能完成查数与归因综合。"""

    models = ScriptedOperationsPlannerModel(
        [
            ModelResponseError("planner timeout"),
            ModelResponseError("planner timeout"),
            ModelResponseError("planner timeout"),
        ],
        summary_answer="已基于拆解树核查营收与关联维度数据。",
    )
    gateway = RichOperationsGateway()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(cast(Any, gateway)):
        registry.register(definition)
    supervisor = Supervisor(cast(ModelGateway, models), registry)
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="本月营收为什么下滑了？排查一下原因",
            gateway_context=GatewayRequestContext("signed", "req-diag-fb", "trace-diag-fb"),
            conversation_id="ops-diag-fallback",
            identity=identity,
        )
    )

    assert response.route == "OPERATIONS"
    assert response.tool_steps >= 3
    assert "REVENUE_AMOUNT" in {call[1] for call in gateway.calls}
    assert "NEW_CUSTOMER_COUNT" in {call[1] for call in gateway.calls}
    assert models.summary_calls == 1


async def test_diagnosis_rejects_student_and_multi_org_before_any_llm_call() -> None:
    """越权学员或多门店管理员触发经营诊断时，在任何规划模型调用前即被拦截。"""

    models = ScriptedOperationsPlannerModel([], summary_answer="unreachable")
    gateway = RichOperationsGateway()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(cast(Any, gateway)):
        registry.register(definition)
    supervisor = Supervisor(cast(ModelGateway, models), registry)

    student = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    with pytest.raises(SupervisorRuntimeError, match="Supervisor 执行失败"):
        await supervisor.invoke(
            SupervisorRequest(
                user_message="本月营收为什么跌了？帮我排查原因",
                gateway_context=GatewayRequestContext("signed", "req-stu-diag", "trace-stu-diag"),
                conversation_id="ops-diag-student",
                identity=student,
            )
        )

    multi_org_admin = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1", "org-2"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )
    with pytest.raises(SupervisorRuntimeError, match="无法安全准备经营查询"):
        await supervisor.invoke(
            SupervisorRequest(
                user_message="本月营收为什么跌了？帮我排查原因",
                gateway_context=GatewayRequestContext("signed", "req-org-diag", "trace-org-diag"),
                conversation_id="ops-diag-multiorg",
                identity=multi_org_admin,
            )
        )

    assert models.json_calls == 0
    assert models.summary_calls == 0
    assert gateway.calls == []


async def test_multi_metric_operations_question_collapses_into_single_operations_route() -> None:
    """同一句话问多个经营指标（无论规则还是 LLM 路由）均收敛为单次 OPERATIONS 路由。"""

    from app.agent.routing import HybridIntentRouter, RoutedIntent, deterministic_route_decision

    decision = deterministic_route_decision("看看本月营收、新客量和完课量怎么样")
    assert decision is not None
    assert decision.intents == (RoutedIntent("OPERATIONS", "QUERY_OPERATIONS_METRIC"),)

    llm_router = HybridIntentRouter(
        cast(
            ModelGateway,
            ScriptedOperationsPlannerModel(
                [
                    (
                        '{"intents":['
                        '{"route":"OPERATIONS","intent":"QUERY_REVENUE"},'
                        '{"route":"OPERATIONS","intent":"QUERY_COMPLETED_CLASS_COUNT"}'
                        '],"confidence":0.95,"reason_code":"MULTI_OPS"}'
                    )
                ],
                summary_answer="",
            ),
        ),
        mode="hybrid",
    )
    llm_decision = await llm_router.decide(
        "近两周卖课回款和消课怎么样？",
        recent_messages=[],
        previous_route=None,
    )
    assert llm_decision.intents == (RoutedIntent("OPERATIONS", "QUERY_OPERATIONS_METRIC"),)
