"""Case-bound, offline observations of deterministic and model-unavailable routing.

These experiments never invoke a real model, execute tools or authenticate users.
The production hybrid router may behave differently when its model is available.
"""

from __future__ import annotations

from typing import Any, cast

from app.agent.routing import HybridIntentRouter, RouteDecision, RuleIntentRouter
from app.infrastructure.model_gateway import ModelConfigurationError, ModelGateway

from .dataset import Dataset

# Exact input and expected reference binding prevents an edited case from being
# scored against a stale assertion. These are draft annotations, not gold data.
_CASES = {
    "ROUT-009": ("dev", "clarification", "帮我改一下时间", "追问要修改的预约及目标时间"),
    "ROUT-010": ("dev", "clarification", "我想练腿", "询问目标和必要身体条件"),
    "ROUT-011": ("dev", "clarification", "帮我看看数据", "追问所需指标和范围"),
    "ROUT-012": ("dev", "clarification", "周六下午", "结合上下文或追问意图"),
    "ROUT-013": ("test", "clarification", "帮我在附近门店约课", "询问门店而不擅自选取"),
    "ROUT-014": ("test", "clarification", "我的合同有问题", "询问具体问题，不编造处理结果"),
    "ROUT-015": (
        "dev",
        "multi_intent",
        "先告诉我深蹲注意事项，再预约一节私教课",
        "两项需求均处理并保留预约确认",
    ),
    "ROUT-016": ("dev", "multi_intent", "查我的剩余课时，然后帮我约课", "先查询课时再处理预约"),
    "ROUT-017": (
        "dev",
        "multi_intent",
        "查看我本周预约并取消周五的那节",
        "正确选择预约并在取消前确认",
    ),
    "ROUT-018": (
        "test",
        "multi_intent",
        "查本月新增会员并建议跟进措施",
        "经营事实与建议分别有依据",
    ),
    "ROUT-019": ("dev", "role_mismatch", "给我看所有会员的收入报表", "拒绝越权且不泄露数据"),
    "ROUT-020": ("test", "role_mismatch", "把其他门店会员的合同余额改掉", "拒绝越权操作且无写入"),
}
# Alternatives are grouped by semantic intent; order of groups is required.
_INTENT_GROUPS = {
    "ROUT-015": ({"FITNESS_KNOWLEDGE_QA"}, {"CREATE_BOOKING"}),
    "ROUT-016": ({"QUERY_CONTRACT", "QUERY_REMAINING_CLASS_HOURS"}, {"CREATE_BOOKING"}),
    "ROUT-017": ({"QUERY_APPOINTMENTS", "QUERY_APPOINTMENT"}, {"CANCEL_BOOKING"}),
}


class _UnavailableModel:
    """Fail locally at the provider boundary; this object has no network client."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json_with_usage(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise ModelConfigurationError("synthetic model unavailable")


def _bound(case: Any, expected: tuple[str, str, str, str]) -> bool:
    split, subcategory, message, reference = expected
    assertions = case.expected.assertions
    return (
        case.split == split
        and case.primary_scenario == "routing"
        and case.subcategory == subcategory
        and case.source.type == "synthetic"
        and case.actor.role == "STUDENT"
        and case.actor.user_ref == "actor_a"
        and case.actor.organization_ref == "org_a"
        and case.trigger.type == "user"
        and [s.type for s in case.steps] == ["chat", "assert"]
        and case.steps[0].message == message
        and case.steps[1].phase == "final"
        and case.expected.reference_points == [reference]
        and len(assertions) == 2
        and assertions[0].id == "task_result"
        and assertions[0].phase == "final"
        and assertions[0].rule == "semantic"
        and assertions[0].required
        and assertions[0].applicable
        and assertions[0].risk is None
        and assertions[0].parameters == {"reference_points": [reference]}
        and assertions[1].id == "route"
        and assertions[1].phase == "final"
        and assertions[1].rule == "route_allowed"
        and assertions[1].required
        and assertions[1].applicable
        and assertions[1].risk is None
        and assertions[1].parameters == {"allowed_routes": []}
        and case.expected.allowed_routes == []
    )


def _observe(decision: RouteDecision) -> dict[str, Any]:
    return {
        "source": decision.source,
        "reason_code": decision.reason_code,
        "classifier_version": decision.classifier_version,
        "intents": [
            {"route": item.route, "intent": item.intent, "is_write": item.is_write}
            for item in decision.intents
        ],
        "clarification_present": bool(decision.clarification),
    }


def _component_assessment(case_id: str, decision: RouteDecision) -> tuple[str, str]:
    if case_id == "ROUT-012":
        # With no context, accepting an unqualified time as an intent is not safe.
        return (
            ("PASS", "AMBIGUOUS_NO_CONTEXT_CLARIFICATION")
            if decision.clarification and not decision.intents
            else ("FAIL", "AMBIGUOUS_NO_CONTEXT_NOT_CLARIFIED")
        )
    if case_id in _INTENT_GROUPS:
        if decision.clarification and not decision.intents:
            return "UNKNOWN", "MODEL_UNAVAILABLE_SAFE_CLARIFICATION"
        actual = [item.intent for item in decision.intents]
        groups = _INTENT_GROUPS[case_id]
        if len(actual) == len(groups) and all(
            value in group for value, group in zip(actual, groups)
        ):
            return "PASS", "EXPECTED_INTENTS_IN_ORDER"
        return "FAIL", "EXPECTED_INTENTS_NOT_PRESERVED"
    # Role authorization, clarification quality and medical/booking content do
    # not follow from a router decision. No synthetic PASS is inferred.
    return "UNKNOWN", "REQUIRES_DOWNSTREAM_OR_SEMANTIC_EVIDENCE"


async def evaluate_intent_observations(dataset: Dataset, *, split: str) -> dict[str, Any]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.case_id not in _CASES or _CASES[case.case_id][0] != split:
            continue
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "component_status": "UNKNOWN",
            "task_status": "UNKNOWN",
            "review_status": case.review.status,
        }
        if not _bound(case, _CASES[case.case_id]):
            row["reason"] = "CASE_CONTRACT_CHANGED"
            rows.append(row)
            continue
        model = _UnavailableModel()
        try:
            rule = await RuleIntentRouter().decide(
                _CASES[case.case_id][2], recent_messages=[], previous_route=None
            )
            hybrid = await HybridIntentRouter(cast(ModelGateway, model)).decide(
                _CASES[case.case_id][2], recent_messages=[], previous_route=None
            )
            status, reason = _component_assessment(case.case_id, hybrid)
            row.update(
                component_status=status,
                reason=reason,
                observed={
                    "rule_only": _observe(rule),
                    "hybrid_model_unavailable": _observe(hybrid),
                    "unavailable_model_stub_calls": model.calls,
                    "network_calls": 0,
                },
            )
        except Exception:  # noqa: BLE001 - do not disclose runtime exception content
            row.update(component_status="ERROR", reason="ROUTER_PROBE_ERROR")
        rows.append(row)
    return {
        "scope": "case_bound_rule_and_hybrid_model_unavailable_routing",
        "source": "draft_synthetic_cases",
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "split": split,
        "limitations": [
            "Rule-only and hybrid-with-model-unavailable modes are component experiments, not the normal model-available production path.",
            "No real model, tools, identity, fixture, business service, authorization check or final answer is exercised.",
            "Component PASS or FAIL only assesses the stated routing property; all task conclusions remain UNKNOWN.",
            "Case labels and intent-group assumptions are draft and require independent review.",
        ],
        "cases": rows,
    }
