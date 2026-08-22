"""Operations Agent 从自然语言到受控工具调用的端到端单元联调。"""

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
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn


class FakeOperationsModels:
    """模拟支持 Tool Calling 的模型，只验证 Supervisor 给出的工具上下文。"""

    def __init__(self) -> None:
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
        if self.turn_index == 0:
            assert any("当前路由=OPERATIONS" in str(message.get("content")) for message in messages)
            assert any("REVENUE_AMOUNT" in str(tool) for tool in tools)
            assert [tool["function"]["name"] for tool in tools] == [
                "fitness_operations_metric_query_v1"
            ]
            self.turn_index += 1
            return ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="operations-call-1",
                        name="fitness.operations.metric.query.v1",
                        arguments={
                            "organization_id": "org-1",
                            "metric": "REVENUE_AMOUNT",
                            "from": "2026-08-01",
                            "to": "2026-08-15",
                            "bucket": "WEEK",
                            "comparison": "NONE",
                        },
                    ),
                ),
                input_tokens=20,
                output_tokens=6,
            )
        self.turn_index += 1
        assert tools == []
        return ModelTurn(
            content="本月营收金额为 30000，按周呈上升趋势。",
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
    """验证学员不能通过自然语言绕过 Operations 的管理员工具权限。

    这里故意让模型返回合法的固定指标工具调用，测试重点不是模型是否会选错工具，
    而是 ToolRegistry 是否在真实 Supervisor 链路中先校验签名身份，再决定是否允许
    触达 Java Gateway。若权限校验位置错误，测试中的 Gateway 会留下调用记录。
    """

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

    with pytest.raises(SupervisorRuntimeError, match="supervisor execution failed"):
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

    assert gateway.calls == []
    assert audit.events == []
