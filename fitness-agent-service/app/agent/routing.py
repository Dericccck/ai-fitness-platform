"""Supervisor 的上下文感知混合意图路由。

路由只决定“进入哪个领域、处理哪个意图”，绝不替代 AgentContext、Tool Registry、
确认凭证或 Java Gateway 的授权。明确且高风险的意图由确定性规则处理；自然语言
省略、同义表达和多意图请求才交给结构化 LLM 分类。模型不可用或低置信度时，系统
要求用户澄清，而不是静默猜测并暴露错误领域工具。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.metrics import HttpMetrics
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
)

from .domain_subgraphs import SupervisorRoute

RouteSource = Literal[
    "HARD_GUARD",
    "DETERMINISTIC_RULE",
    "CONTEXT_INHERITANCE",
    "LLM",
    "LLM_SHADOW",
    "FALLBACK",
]

_EXECUTABLE_ROUTES = frozenset({"FITNESS_COACHING", "BOOKING", "OPERATIONS", "CUSTOMER_SERVICE"})

# 写意图由服务端固定枚举决定，不能信任模型自行声明 read_only。
WRITE_INTENTS = frozenset(
    {
        "CREATE_BOOKING",
        "RESCHEDULE_BOOKING",
        "CANCEL_BOOKING",
        "CREATE_TRAINING_DRAFT",
        "SUBMIT_PLAN_REVIEW",
        "REVIEW_PLAN",
        "PUBLISH_PLAN",
        "RECORD_EXECUTION",
        "SAVE_MEMORY",
        "REVOKE_MEMORY",
        "CREATE_TICKET",
    }
)

INTENTS_BY_ROUTE: dict[str, frozenset[str]] = {
    "FITNESS_COACHING": frozenset(
        {
            "FITNESS_KNOWLEDGE_QA",
            "GENERATE_TRAINING_DRAFT",
            "CREATE_TRAINING_DRAFT",
            "QUERY_TRAINING_PLAN",
            "SUBMIT_PLAN_REVIEW",
            "REVIEW_PLAN",
            "PUBLISH_PLAN",
            "QUERY_EXECUTION",
            "RECORD_EXECUTION",
            "LIST_MEMORY",
            "SAVE_MEMORY",
            "REVOKE_MEMORY",
        }
    ),
    "BOOKING": frozenset(
        {
            "QUERY_APPOINTMENTS",
            "QUERY_AVAILABILITY",
            "CREATE_BOOKING",
            "RESCHEDULE_BOOKING",
            "CANCEL_BOOKING",
        }
    ),
    "OPERATIONS": frozenset(
        {
            "QUERY_REVENUE",
            "QUERY_APPOINTMENT_COUNT",
            "QUERY_APPOINTMENT_STATUS",
            "QUERY_COMPLETED_CLASS_COUNT",
            "QUERY_NEW_CUSTOMER_COUNT",
            "QUERY_COURSE_APPOINTMENT_COUNT",
            "QUERY_COACH_APPOINTMENT_COUNT",
            "QUERY_REMAINING_CLASS_HOURS",
            "QUERY_OPERATIONS_METRIC",
        }
    ),
    "CUSTOMER_SERVICE": frozenset(
        {
            "QUERY_CONTRACT",
            "QUERY_APPOINTMENT",
            "QUERY_PLAN_STATUS",
            "QUERY_TICKET",
            "LIST_TICKETS",
            "CREATE_TICKET",
            "BUSINESS_RULE_QA",
            "RESTRICTED_MEDICAL",
            "RESTRICTED_REFUND_DISPUTE",
        }
    ),
}

# 意图级白名单进一步收窄领域白名单。未列出的兼容旧意图仍会退回领域白名单，
# 但所有 v3 路由结果都必须通过 INTENTS_BY_ROUTE 校验，因此不会走该兼容分支。
TOOLS_BY_INTENT: dict[str, frozenset[str]] = {
    "FITNESS_KNOWLEDGE_QA": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.memory.list.v1",
        }
    ),
    "GENERATE_TRAINING_DRAFT": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.training.plan.get.v1",
            "fitness.memory.list.v1",
            "fitness.training.plan.generate_draft.v1",
        }
    ),
    "CREATE_TRAINING_DRAFT": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.training.plan.get.v1",
            "fitness.memory.list.v1",
            "fitness.training.plan.generate_draft.v1",
            "fitness.training.plan.create_draft.v1",
        }
    ),
    "QUERY_TRAINING_PLAN": frozenset({"fitness.training.plan.get.v1"}),
    "SUBMIT_PLAN_REVIEW": frozenset(
        {"fitness.training.plan.get.v1", "fitness.training.plan.submit_review.v1"}
    ),
    "REVIEW_PLAN": frozenset({"fitness.training.plan.get.v1", "fitness.training.plan.review.v1"}),
    "PUBLISH_PLAN": frozenset({"fitness.training.plan.get.v1", "fitness.training.plan.publish.v1"}),
    "QUERY_EXECUTION": frozenset(
        {"fitness.training.plan.get.v1", "fitness.training.day.executions.list.v1"}
    ),
    "RECORD_EXECUTION": frozenset(
        {
            "fitness.training.plan.get.v1",
            "fitness.training.day.executions.list.v1",
            "fitness.training.day.record_execution.v1",
        }
    ),
    "LIST_MEMORY": frozenset({"fitness.memory.list.v1"}),
    "SAVE_MEMORY": frozenset({"fitness.memory.list.v1", "fitness.memory.save.v1"}),
    "REVOKE_MEMORY": frozenset({"fitness.memory.list.v1", "fitness.memory.revoke.v1"}),
    "QUERY_APPOINTMENTS": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.appointment.list.v1",
        }
    ),
    "QUERY_AVAILABILITY": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.booking.availability.check.v1",
        }
    ),
    "CREATE_BOOKING": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.booking.availability.check.v1",
            "fitness.booking.create.v1",
        }
    ),
    "RESCHEDULE_BOOKING": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.appointment.list.v1",
            "fitness.booking.availability.check.v1",
            "fitness.booking.reschedule.v1",
        }
    ),
    "CANCEL_BOOKING": frozenset({"fitness.appointment.list.v1", "fitness.booking.cancel.v1"}),
    "QUERY_REVENUE": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_APPOINTMENT_COUNT": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_APPOINTMENT_STATUS": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_COMPLETED_CLASS_COUNT": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_NEW_CUSTOMER_COUNT": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_COURSE_APPOINTMENT_COUNT": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_COACH_APPOINTMENT_COUNT": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_REMAINING_CLASS_HOURS": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_OPERATIONS_METRIC": frozenset({"fitness.operations.metric.query.v1"}),
    "QUERY_CONTRACT": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
        }
    ),
    "QUERY_APPOINTMENT": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.appointment.list.v1",
        }
    ),
    "QUERY_PLAN_STATUS": frozenset(
        {"fitness.training.plan.get.v1", "fitness.training.day.executions.list.v1"}
    ),
    "QUERY_TICKET": frozenset({"fitness.support.ticket.list.v1", "fitness.support.ticket.get.v1"}),
    "LIST_TICKETS": frozenset({"fitness.support.ticket.list.v1"}),
    "CREATE_TICKET": frozenset(
        {
            "fitness.user.get_current.v1",
            "fitness.organization.get.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
            "fitness.support.ticket.list.v1",
            "fitness.support.ticket.create.v1",
        }
    ),
    "BUSINESS_RULE_QA": frozenset(
        {
            "fitness.organization.get.v1",
            "fitness.course.list.v1",
            "fitness.contract.list.v1",
            "fitness.appointment.list.v1",
        }
    ),
    "RESTRICTED_MEDICAL": frozenset(),
    "RESTRICTED_REFUND_DISPUTE": frozenset(),
}

FORCED_TOOL_BY_INTENT: dict[str, str] = {
    "CREATE_BOOKING": "fitness_booking_create_v1",
    "RESCHEDULE_BOOKING": "fitness_booking_reschedule_v1",
    "CANCEL_BOOKING": "fitness_booking_cancel_v1",
    "CREATE_TICKET": "fitness_support_ticket_create_v1",
    "SUBMIT_PLAN_REVIEW": "fitness_training_plan_submit_review_v1",
    "REVIEW_PLAN": "fitness_training_plan_review_v1",
    "PUBLISH_PLAN": "fitness_training_plan_publish_v1",
    "RECORD_EXECUTION": "fitness_training_day_record_execution_v1",
    "SAVE_MEMORY": "fitness_memory_save_v1",
    "REVOKE_MEMORY": "fitness_memory_revoke_v1",
}


@dataclass(frozen=True)
class RoutedIntent:
    """一个经过服务端校验的领域意图。"""

    route: SupervisorRoute
    intent: str

    @property
    def is_write(self) -> bool:
        return self.intent in WRITE_INTENTS

    def as_state(self) -> dict[str, object]:
        return {"route": self.route, "intent": self.intent, "is_write": self.is_write}


@dataclass(frozen=True)
class RouteDecision:
    """一次路由判断的稳定结果；不包含模型思维链或原始敏感上下文。"""

    intents: tuple[RoutedIntent, ...]
    source: RouteSource
    confidence: float
    reason_code: str
    classifier_version: str
    clarification: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def primary_route(self) -> SupervisorRoute:
        return self.intents[0].route if self.intents else "FITNESS_COACHING"


class IntentRouter(Protocol):
    async def decide(
        self,
        user_message: str,
        *,
        recent_messages: list[dict[str, Any]],
        previous_route: str | None,
    ) -> RouteDecision: ...


class _ModelIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    intent: str


class _ModelRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intents: list[_ModelIntent] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")


class RuleIntentRouter:
    """无额外模型调用的兼容路由器，主要用于离线测试和显式规则模式。"""

    async def decide(
        self,
        user_message: str,
        *,
        recent_messages: list[dict[str, Any]],
        previous_route: str | None,
    ) -> RouteDecision:
        del recent_messages
        decision = deterministic_route_decision(user_message)
        if decision is not None:
            return decision
        inherited = _context_inheritance_decision(user_message, previous_route)
        if inherited is not None:
            return inherited
        return RouteDecision(
            intents=(RoutedIntent("FITNESS_COACHING", "FITNESS_KNOWLEDGE_QA"),),
            source="FALLBACK",
            confidence=0.5,
            reason_code="RULE_DEFAULT_FITNESS",
            classifier_version="rules-v2",
        )


class HybridIntentRouter:
    """明确规则直达、模糊请求使用结构化 LLM 的生产路由器。"""

    def __init__(
        self,
        models: ModelGateway,
        *,
        mode: Literal["rules", "shadow", "hybrid"] = "hybrid",
        min_confidence: float = 0.8,
        timeout_seconds: float = 3.0,
        max_output_tokens: int = 220,
        context_messages: int = 6,
        metrics: HttpMetrics | None = None,
    ) -> None:
        self.models = models
        self.mode = mode
        self.min_confidence = min_confidence
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.context_messages = context_messages
        self.metrics = metrics

    async def decide(
        self,
        user_message: str,
        *,
        recent_messages: list[dict[str, Any]],
        previous_route: str | None,
    ) -> RouteDecision:
        deterministic = deterministic_route_decision(user_message)
        if self.mode == "rules":
            return deterministic or await RuleIntentRouter().decide(
                user_message,
                recent_messages=recent_messages,
                previous_route=previous_route,
            )

        # 高风险写入、明确经营指标和明确多意图由规则直接决定，避免一次不必要的
        # 供应商调用，也避免 LLM 把已明确的写意图降级成普通问答。
        if deterministic is not None and (
            any(intent.is_write for intent in deterministic.intents)
            or len(deterministic.intents) > 1
            or deterministic.confidence >= 0.98
        ):
            self._record(deterministic, "ACCEPTED")
            return deterministic

        inherited = _context_inheritance_decision(user_message, previous_route)
        try:
            llm_decision = await asyncio.wait_for(
                self._classify_with_llm(user_message, recent_messages, previous_route),
                timeout=self.timeout_seconds,
            )
        except (
            TimeoutError,
            ModelConfigurationError,
            ModelResponseError,
            ValidationError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            fallback = (
                deterministic
                or inherited
                or _clarification_decision(
                    "ROUTER_UNAVAILABLE",
                    "我还不能确定你要处理的是预约、训练计划、经营数据还是客服问题，请再具体说明一下。",
                )
            )
            _logger.warning(
                "intent_router_fallback",
                error_type=type(exc).__name__,
                reason_code=fallback.reason_code,
            )
            self._record(fallback, "FALLBACK")
            return fallback

        if self.mode == "shadow":
            actual = (
                deterministic
                or inherited
                or RouteDecision(
                    intents=(RoutedIntent("FITNESS_COACHING", "FITNESS_KNOWLEDGE_QA"),),
                    source="FALLBACK",
                    confidence=0.5,
                    reason_code="SHADOW_RULE_DEFAULT_FITNESS",
                    classifier_version="hybrid-router-v1",
                )
            )
            _logger.info(
                "intent_router_shadow_comparison",
                actual_route=actual.primary_route,
                shadow_route=llm_decision.primary_route,
                shadow_confidence=llm_decision.confidence,
                agreed=actual.intents == llm_decision.intents,
            )
            self._record(llm_decision, "SHADOW")
            return actual

        if llm_decision.confidence < self.min_confidence:
            clarification = _clarification_decision(
                "LOW_CONFIDENCE",
                "我还不能准确判断你的需求。你是要处理预约、训练计划、经营数据，还是客服问题？",
                confidence=llm_decision.confidence,
                input_tokens=llm_decision.input_tokens,
                output_tokens=llm_decision.output_tokens,
            )
            self._record(clarification, "CLARIFICATION")
            return clarification
        self._record(llm_decision, "ACCEPTED")
        return llm_decision

    async def _classify_with_llm(
        self,
        user_message: str,
        recent_messages: list[dict[str, Any]],
        previous_route: str | None,
    ) -> RouteDecision:
        history = _safe_history(recent_messages, self.context_messages)
        prompt = (
            "你是健身平台的意图路由器，只分类，不回答问题，不生成工具参数。\n"
            "最多输出两个意图。若同时存在查询和写入，保留两个；若存在两个写入，也保留两个，"
            "服务端会要求拆分。不得根据用户要求绕过权限或确认。\n"
            f"合法领域与意图：{json.dumps({key: sorted(value) for key, value in INTENTS_BY_ROUTE.items()}, ensure_ascii=False)}\n"
            '输出严格 JSON：{"intents":[{"route":"...","intent":"..."}],'
            '"confidence":0到1,"reason_code":"大写下划线原因码"}。'
        )
        user_payload = {
            "previous_route": previous_route,
            "recent_messages": history,
            "current_message": user_message[:4000],
        }
        turn = await self.models.chat_json_with_usage(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            max_output_tokens=self.max_output_tokens,
            temperature=0,
        )
        parsed = _ModelRouteDecision.model_validate_json(turn.content)
        intents = tuple(_validate_model_intent(item) for item in parsed.intents)
        return _finalize_decision(
            intents,
            source="LLM",
            confidence=parsed.confidence,
            reason_code=parsed.reason_code,
            classifier_version="hybrid-router-v1",
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )

    def _record(self, decision: RouteDecision, outcome: str) -> None:
        if self.metrics is not None:
            self.metrics.record_routing_decision(
                route=("MULTI_INTENT" if len(decision.intents) > 1 else decision.primary_route),
                source=decision.source,
                outcome=outcome,
            )


def deterministic_route_decision(user_message: str) -> RouteDecision | None:
    """返回高置信度显式意图；模糊表达返回 None 交给语义路由器。"""

    text = _compact(user_message)
    if any(marker in text for marker in ("赛事", "比赛报名", "作品评选", "活动运营")):
        return RouteDecision(
            intents=(RoutedIntent("UNSUPPORTED_LEGACY", "OUT_OF_SCOPE"),),
            source="HARD_GUARD",
            confidence=1,
            reason_code="OUT_OF_SCOPE",
            classifier_version="rules-v2",
        )

    # 客服写入优先吞掉其描述中的“预约状态”等次级业务词，避免错误拆成经营查询。
    if _affirmative(text, ("提交工单", "创建工单", "提交客服工单", "帮我反馈问题")):
        return _single("CUSTOMER_SERVICE", "CREATE_TICKET", "EXPLICIT_TICKET_WRITE")

    signals: list[RoutedIntent] = []
    _append_if(
        signals,
        text,
        "BOOKING",
        "RESCHEDULE_BOOKING",
        ("改约", "修改预约", "调整预约", "把课挪", "课程挪", "预约改到"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "BOOKING",
        "CANCEL_BOOKING",
        ("取消预约", "撤销预约", "取消这节课"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "BOOKING",
        "CREATE_BOOKING",
        ("创建预约", "帮我预约", "预约一次", "预订课程", "预定课程"),
        write=True,
    )
    _append_if(
        signals, text, "FITNESS_COACHING", "PUBLISH_PLAN", ("发布训练计划", "发布计划"), write=True
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "SUBMIT_PLAN_REVIEW",
        ("提交训练计划审核", "训练计划提交审核", "计划提交审核"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "REVIEW_PLAN",
        ("审核训练计划", "驳回训练计划", "通过训练计划"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "RECORD_EXECUTION",
        ("记录训练完成", "记录已完成", "记录训练跳过", "今天训练完成"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "SAVE_MEMORY",
        ("记住我", "保存我的偏好", "保存训练偏好"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "REVOKE_MEMORY",
        ("忘掉我的", "删除我的偏好", "撤销记忆"),
        write=True,
    )
    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "CREATE_TRAINING_DRAFT",
        (
            "创建训练计划",
            "制定训练计划",
            "生成训练计划",
            "制定一个",
            "制定力量计划",
            "创建力量计划",
        ),
        write=True,
    )
    if (
        "计划" in text
        and any(noun in text for noun in ("训练", "力量", "减脂", "增肌"))
        and _affirmative(text, ("创建", "制定", "生成", "设计", "安排"))
    ):
        signals.append(RoutedIntent("FITNESS_COACHING", "CREATE_TRAINING_DRAFT"))

    _append_if(
        signals,
        text,
        "BOOKING",
        "QUERY_AVAILABILITY",
        ("可约时间", "空闲时段", "什么时候能约", "有空的教练"),
    )
    _append_if(
        signals,
        text,
        "CUSTOMER_SERVICE",
        "QUERY_APPOINTMENT",
        ("我的预约", "查看预约", "预约是什么时间", "预约到几点"),
    )
    _append_if(
        signals,
        text,
        "CUSTOMER_SERVICE",
        "QUERY_CONTRACT",
        ("我的合同", "私教包", "剩几节", "还剩几次", "合同还有"),
    )
    _append_if(
        signals,
        text,
        "CUSTOMER_SERVICE",
        "QUERY_TICKET",
        ("查询工单", "我的客服工单", "我的工单", "工单进度", "工单状态", "上次工单"),
    )
    _append_if(
        signals,
        text,
        "CUSTOMER_SERVICE",
        "QUERY_PLAN_STATUS",
        ("训练计划状态", "计划状态", "训练执行记录"),
    )
    _append_if(
        signals, text, "CUSTOMER_SERVICE", "QUERY_CONTRACT", ("查询课程", "查看课程", "我的课程")
    )

    operations = (
        ("QUERY_REVENUE", ("营收", "营业收入", "店里进账", "门店进账")),
        ("QUERY_APPOINTMENT_COUNT", ("预约量", "预约数", "预约总量")),
        ("QUERY_APPOINTMENT_STATUS", ("预约状态分布", "预约状态统计")),
        ("QUERY_COMPLETED_CLASS_COUNT", ("完课量", "完课数")),
        ("QUERY_NEW_CUSTOMER_COUNT", ("新客量", "新客数", "新增客户")),
        ("QUERY_COURSE_APPOINTMENT_COUNT", ("课程预约量", "课程利用")),
        ("QUERY_COACH_APPOINTMENT_COUNT", ("教练预约量", "教练工作量", "教练表现")),
        ("QUERY_REMAINING_CLASS_HOURS", ("剩余课时统计", "课时余额统计")),
    )
    for intent, markers in operations:
        _append_if(signals, text, "OPERATIONS", intent, markers)

    _append_if(
        signals,
        text,
        "FITNESS_COACHING",
        "GENERATE_TRAINING_DRAFT",
        ("训练计划建议", "训练方案预览"),
    )
    if not any(signal.route == "FITNESS_COACHING" and signal.is_write for signal in signals):
        _append_if(
            signals,
            text,
            "FITNESS_COACHING",
            "FITNESS_KNOWLEDGE_QA",
            ("热身", "深蹲", "硬拉", "拉伸", "增肌", "减脂", "训练动作", "怎么练"),
        )

    signals = _deduplicate(signals)
    if not signals:
        return None
    return _finalize_decision(
        tuple(signals),
        source="DETERMINISTIC_RULE",
        confidence=0.99,
        reason_code=("EXPLICIT_MULTI_INTENT" if len(signals) > 1 else "EXPLICIT_INTENT"),
        classifier_version="rules-v2",
    )


def intent_allowed_tool_ids(route: SupervisorRoute, intent: str | None) -> frozenset[str] | None:
    """返回意图级工具集合；旧 Checkpoint 没有 intent 时返回 None 保持兼容。"""

    if not intent:
        return None
    allowed = TOOLS_BY_INTENT.get(intent)
    if allowed is None or intent not in INTENTS_BY_ROUTE.get(route, frozenset()):
        raise ValueError("领域意图与工具白名单不匹配")
    return allowed


def forced_tool_for_intent(intent: str | None) -> str | None:
    return FORCED_TOOL_BY_INTENT.get(intent or "")


def intent_display_name(intent: str) -> str:
    names = {
        "FITNESS_KNOWLEDGE_QA": "健身建议",
        "GENERATE_TRAINING_DRAFT": "训练计划建议",
        "CREATE_TRAINING_DRAFT": "创建训练计划",
        "QUERY_TRAINING_PLAN": "训练计划",
        "QUERY_APPOINTMENTS": "预约查询",
        "QUERY_AVAILABILITY": "可约时间",
        "CREATE_BOOKING": "创建预约",
        "RESCHEDULE_BOOKING": "修改预约",
        "CANCEL_BOOKING": "取消预约",
        "QUERY_REVENUE": "营收查询",
        "QUERY_CONTRACT": "合同与课时",
        "QUERY_APPOINTMENT": "预约信息",
        "QUERY_TICKET": "客服工单",
        "CREATE_TICKET": "创建客服工单",
    }
    return names.get(intent, intent.replace("_", " ").title())


def _single(route: SupervisorRoute, intent: str, reason_code: str) -> RouteDecision:
    return RouteDecision(
        intents=(RoutedIntent(route, intent),),
        source="DETERMINISTIC_RULE",
        confidence=0.99,
        reason_code=reason_code,
        classifier_version="rules-v2",
    )


def _append_if(
    target: list[RoutedIntent],
    text: str,
    route: SupervisorRoute,
    intent: str,
    markers: tuple[str, ...],
    *,
    write: bool = False,
) -> None:
    matched = _affirmative(text, markers) if write else any(marker in text for marker in markers)
    if matched:
        target.append(RoutedIntent(route, intent))


def _affirmative(text: str, markers: tuple[str, ...]) -> bool:
    negations = ("不要", "别", "无需", "不用", "不需要", "禁止", "不想", "不是要", "并非要")
    boundaries = "，。；！？\n"
    for marker in markers:
        start = 0
        while (index := text.find(marker, start)) >= 0:
            boundary = max(text.rfind(item, 0, index) for item in boundaries)
            if not any(word in text[boundary + 1 : index] for word in negations):
                return True
            start = index + len(marker)
    return False


def _compact(value: str) -> str:
    return "".join(value.lower().split())


def _deduplicate(intents: list[RoutedIntent]) -> list[RoutedIntent]:
    result: list[RoutedIntent] = []
    seen: set[tuple[str, str]] = set()
    for item in intents:
        key = (item.route, item.intent)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _context_inheritance_decision(
    user_message: str, previous_route: str | None
) -> RouteDecision | None:
    if previous_route not in _EXECUTABLE_ROUTES:
        return None
    text = _compact(user_message)
    if len(text) > 40 or not any(
        marker in text
        for marker in ("那", "这个", "那个", "还有呢", "换一个", "改到", "继续", "怎么样")
    ):
        return None
    default_intent = {
        "FITNESS_COACHING": "FITNESS_KNOWLEDGE_QA",
        "BOOKING": "QUERY_APPOINTMENTS",
        "OPERATIONS": "QUERY_OPERATIONS_METRIC",
        "CUSTOMER_SERVICE": "BUSINESS_RULE_QA",
    }[previous_route]
    return RouteDecision(
        intents=(RoutedIntent(previous_route, default_intent),),  # type: ignore[arg-type]
        source="CONTEXT_INHERITANCE",
        confidence=0.72,
        reason_code="ELLIPTICAL_FOLLOW_UP",
        classifier_version="hybrid-router-v1",
    )


def _validate_model_intent(item: _ModelIntent) -> RoutedIntent:
    if item.route not in _EXECUTABLE_ROUTES:
        raise ValueError("模型返回了不支持的领域")
    if item.intent not in INTENTS_BY_ROUTE[item.route]:
        raise ValueError("模型返回了不支持的领域意图")
    return RoutedIntent(item.route, item.intent)  # type: ignore[arg-type]


def _finalize_decision(
    intents: tuple[RoutedIntent, ...],
    *,
    source: RouteSource,
    confidence: float,
    reason_code: str,
    classifier_version: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RouteDecision:
    intents = tuple(_deduplicate(list(intents)))
    if len(intents) > 2:
        return _clarification_decision(
            "TOO_MANY_INTENTS",
            "一次最多处理两个需求，请先选择最重要的一项。",
            confidence=confidence,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    writes = [item for item in intents if item.is_write]
    if len(writes) > 1:
        return _clarification_decision(
            "MULTIPLE_WRITE_INTENTS",
            "这条消息包含多个写操作。为避免一次确认修改多项业务，请分别发送，每次只处理一个写操作。",
            confidence=confidence,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    # 查询与一个写入同时出现时先完成查询，再为唯一写入生成独立确认卡。
    ordered = tuple([item for item in intents if not item.is_write] + writes)
    return RouteDecision(
        intents=ordered,
        source=source,
        confidence=max(0, min(1, confidence)),
        reason_code=reason_code,
        classifier_version=classifier_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _clarification_decision(
    reason_code: str,
    message: str,
    *,
    confidence: float = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RouteDecision:
    return RouteDecision(
        intents=(),
        source="FALLBACK",
        confidence=confidence,
        reason_code=reason_code,
        classifier_version="hybrid-router-v1",
        clarification=message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _safe_history(messages: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
            continue
        # 路由只需要最近语义，不发送工具结果、系统 Prompt、签名上下文或长篇正文。
        safe.append({"role": role, "content": re.sub(r"\s+", " ", content)[:800]})
    return safe[-limit:]


_logger = structlog.get_logger("agent.intent_router")
