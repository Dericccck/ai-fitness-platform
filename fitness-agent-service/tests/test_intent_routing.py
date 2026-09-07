from __future__ import annotations

from typing import Any, cast

from app.agent.routing import (
    HybridIntentRouter,
    RouteDecision,
    RoutedIntent,
    RuleIntentRouter,
    deterministic_route_decision,
)
from app.agent.supervisor import Supervisor, SupervisorRequest
from app.agent.tool_registry import ToolRegistry
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import (
    JsonModelTurn,
    ModelGateway,
    ModelResponseError,
    ModelTurn,
)


class StaticRouter:
    def __init__(self, decision: RouteDecision) -> None:
        self.decision = decision

    async def decide(self, *_: Any, **__: Any) -> RouteDecision:
        return self.decision


class SequencedModel:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def chat_with_tools(self, messages: list[dict[str, Any]], **_: Any) -> ModelTurn:
        prompt = str(messages[0]["content"])
        self.system_prompts.append(prompt)
        return ModelTurn(content=f"第{len(self.system_prompts)}个查询结果", tool_calls=())


class JsonRouterModel:
    def __init__(self, content: str | None = None, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.messages: list[list[dict[str, Any]]] = []

    async def chat_json_with_usage(self, messages: list[dict[str, Any]], **_: Any) -> JsonModelTurn:
        self.messages.append(messages)
        if self.fail:
            raise ModelResponseError("provider unavailable")
        assert self.content is not None
        return JsonModelTurn(self.content, input_tokens=12, output_tokens=7)


def request(message: str) -> SupervisorRequest:
    return SupervisorRequest(
        user_message=message,
        gateway_context=GatewayRequestContext("signed", "request-1", "trace-1"),
        conversation_id="routing-test",
    )


async def test_rule_router_orders_read_before_the_single_write() -> None:
    decision = await RuleIntentRouter().decide(
        "先查我的预约，然后帮我取消预约",
        recent_messages=[],
        previous_route=None,
    )

    assert [item.intent for item in decision.intents] == [
        "QUERY_APPOINTMENT",
        "CANCEL_BOOKING",
    ]


def test_two_writes_require_split_instead_of_shared_confirmation() -> None:
    decision = deterministic_route_decision("帮我取消预约并创建训练计划")

    assert decision is not None
    assert decision.intents == ()
    assert decision.reason_code == "MULTIPLE_WRITE_INTENTS"
    assert "分别发送" in str(decision.clarification)


async def test_two_read_only_intents_execute_in_order_and_combine_answers() -> None:
    model = SequencedModel()
    decision = RouteDecision(
        intents=(
            RoutedIntent("CUSTOMER_SERVICE", "QUERY_CONTRACT"),
            RoutedIntent("OPERATIONS", "QUERY_REVENUE"),
        ),
        source="LLM",
        confidence=0.94,
        reason_code="TWO_READS",
        classifier_version="test-router",
    )
    supervisor = Supervisor(
        cast(ModelGateway, model),
        ToolRegistry(),
        intent_router=StaticRouter(decision),  # type: ignore[arg-type]
    )

    response = await supervisor.invoke(request("查合同，再看本月营收"))

    assert response.route == "MULTI_INTENT"
    assert response.routes == ("CUSTOMER_SERVICE", "OPERATIONS")
    assert "合同与课时" in response.answer
    assert "营收查询" in response.answer
    assert response.answer.index("第1个查询结果") < response.answer.index("第2个查询结果")
    assert "客服 Agent" in model.system_prompts[0]
    assert "经营分析 Agent" in model.system_prompts[1]


async def test_hybrid_router_uses_validated_llm_result_for_ambiguous_language() -> None:
    model = JsonRouterModel(
        '{"intents":[{"route":"BOOKING","intent":"RESCHEDULE_BOOKING"}],'
        '"confidence":0.93,"reason_code":"SEMANTIC_RESCHEDULE"}'
    )
    router = HybridIntentRouter(cast(ModelGateway, model), min_confidence=0.8)

    decision = await router.decide(
        "把那节课往后挪一点",
        recent_messages=[{"role": "tool", "content": "sensitive"}],
        previous_route="BOOKING",
    )

    assert decision.intents[0].intent == "RESCHEDULE_BOOKING"
    assert decision.source == "LLM"
    assert decision.input_tokens == 12
    assert "sensitive" not in str(model.messages)


async def test_hybrid_router_requires_clarification_on_low_confidence() -> None:
    model = JsonRouterModel(
        '{"intents":[{"route":"BOOKING","intent":"QUERY_APPOINTMENTS"}],'
        '"confidence":0.51,"reason_code":"AMBIGUOUS"}'
    )
    router = HybridIntentRouter(cast(ModelGateway, model), min_confidence=0.8)

    decision = await router.decide("帮我看看那个", recent_messages=[], previous_route=None)

    assert decision.intents == ()
    assert decision.reason_code == "LOW_CONFIDENCE"
    assert decision.clarification


async def test_hybrid_router_fails_safe_when_provider_is_unavailable() -> None:
    router = HybridIntentRouter(cast(ModelGateway, JsonRouterModel(fail=True)))

    decision = await router.decide("帮我处理一下", recent_messages=[], previous_route=None)

    assert decision.intents == ()
    assert decision.reason_code == "ROUTER_UNAVAILABLE"
