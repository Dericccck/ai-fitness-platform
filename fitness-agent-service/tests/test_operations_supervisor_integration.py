"""经营 Agent 从自然语言到受控工具调用的端到端单元联调。"""

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
from app.infrastructure.model_gateway import ModelGateway, ModelTurn


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
