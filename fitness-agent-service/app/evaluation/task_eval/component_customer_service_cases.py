"""Case-bound customer-service routing probes; not full conversation grading."""

from __future__ import annotations

from typing import Any

from app.agent.routing import (
    deterministic_route_decision,
    forced_tool_for_intent,
    intent_allowed_tool_ids,
)

from .dataset import Dataset

_MESSAGE = "教练今天迟到了，我要投诉"
_REFERENCE = "记录或引导投诉，不声称已处理"


def _bound(case: Any) -> bool:
    assertions = case.expected.assertions
    return (
        case.primary_scenario == "operations"
        and case.subcategory == "customer_service"
        and case.actor.role == "STUDENT"
        and case.actor.organization_ref == "org_a"
        and case.actor.user_ref == "actor_a"
        and case.trigger.type == "user"
        and [step.type for step in case.steps] == ["chat", "assert"]
        and case.steps[0].message == _MESSAGE
        and case.steps[1].phase == "final"
        and case.expected.reference_points == [_REFERENCE]
        and len(assertions) == 1
        and assertions[0].phase == "final"
        and assertions[0].rule == "semantic"
        and assertions[0].required
        and assertions[0].applicable
        and assertions[0].parameters.get("reference_points") == [_REFERENCE]
    )


def evaluate_customer_service_case_observations(dataset: Dataset, *, split: str) -> dict[str, Any]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.case_id != "OPER-019" or case.split != split:
            continue
        if not _bound(case):
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "UNKNOWN",
                    "task_status": "UNKNOWN",
                    "reason": "CASE_CONTRACT_CHANGED",
                }
            )
            continue
        try:
            decision = deterministic_route_decision(_MESSAGE)
            intent = decision.intents[0] if decision and len(decision.intents) == 1 else None
            allowed = intent_allowed_tool_ids(intent.route, intent.intent) if intent else None
            forced = forced_tool_for_intent(intent.intent) if intent else None
            observed = {
                "route": intent.route if intent else None,
                "intent": intent.intent if intent else None,
                "forced_tool": forced,
                "ticket_write_available": allowed is not None
                and "fitness.support.ticket.create.v1" in allowed,
            }
            passed = observed == {
                "route": "CUSTOMER_SERVICE",
                "intent": "BUSINESS_RULE_QA",
                "forced_tool": None,
                "ticket_write_available": False,
            }
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "PASS" if passed else "FAIL",
                    "task_status": "UNKNOWN",
                    "reason": "ROUTE_AND_TOOL_ALLOWLIST_ONLY",
                    "observed": observed,
                }
            )
        except Exception:  # noqa: BLE001 - no error contents in report
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "ERROR",
                    "task_status": "UNKNOWN",
                    "reason": "PROBE_ERROR",
                }
            )
    return {
        "scope": "case_bound_customer_service_routing",
        "source": "draft_case_message",
        "limitations": [
            "Only deterministic intent and its tool allowlist are checked; model response, factual accuracy, actual ticket creation and claims are not observed.",
            "Complaint guidance routing is a conservative fixture assumption and needs business review.",
        ],
        "cases": rows,
    }
