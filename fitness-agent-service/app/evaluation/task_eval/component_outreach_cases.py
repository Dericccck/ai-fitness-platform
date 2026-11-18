"""Draft-case-bound checks of isolated outreach guard components.

The planning dataset does not define executable events or recipients. The probes
below supply explicit synthetic inputs, and therefore never mark the task passed.
"""

from __future__ import annotations

from typing import Any

from app.proactive.candidate_engine import _candidate_specs
from app.proactive.events import ProactiveEventMessage, notification_targets

from .component_notification_worker import evaluate_notification_worker
from .dataset import Dataset

_CASES = {
    "OPER-014": "按策略去重，不重复投递",
    "OPER-015": "不向撤回授权的会员投递",
    "OPER-016": "不向不符合条件的成员发送",
}


def _bound(case: Any, reference: str) -> bool:
    assertions = case.expected.assertions
    return (
        case.primary_scenario == "operations"
        and case.subcategory == "outreach"
        and case.actor.role == "ADMIN"
        and case.actor.organization_ref == "org_a"
        and case.actor.user_ref == "actor_a"
        and case.trigger.type == "event"
        and [step.type for step in case.steps] == ["trigger_event", "await_terminal", "assert"]
        and case.steps[0].event_ref == f"planned-{case.case_id.lower()}.event"
        and case.steps[1].target == "event"
        and case.steps[2].phase == "final"
        and case.expected.reference_points == [reference]
        and len(assertions) == 1
        and assertions[0].rule == "notification_policy"
        and assertions[0].phase == "final"
        and assertions[0].required
        and assertions[0].applicable
        and assertions[0].parameters.get("reference_points") == [reference]
    )


def _event(event_type: str, payload: dict[str, Any]) -> ProactiveEventMessage:
    return ProactiveEventMessage(
        event_id="fixture-event",
        source="booking" if event_type.startswith("APPOINTMENT_") else "training",
        event_type=event_type,
        aggregate_id="fixture-plan",
        organization_id="fixture-org",
        payload=payload,
    )


async def evaluate_outreach_case_observations(dataset: Dataset, *, split: str) -> dict[str, Any]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    rows: list[dict[str, Any]] = []
    worker_report: dict[str, Any] | None = None
    for case in dataset.cases:
        reference = _CASES.get(case.case_id)
        if case.split != split or reference is None:
            continue
        if not _bound(case, reference):
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
            if case.case_id == "OPER-014":
                # Same person as coach and student, a narrow target-list dedupe.
                event = _event("APPOINTMENT_CANCELLED", {"studentId": "same", "coachId": "same"})
                targets = notification_targets(event)
                observed: dict[str, Any] = {"recipients": [t.user_id for t in targets]}
                passed = observed["recipients"] == ["same"]
                reason = "RECIPIENT_LIST_ONLY"
            elif case.case_id == "OPER-015":
                # Treat a disabled in-app preference as a simulated opt-out. This
                # cannot establish the mapping from actual consent to preference.
                if worker_report is None:
                    worker_report = await evaluate_notification_worker()
                worker_row = next(r for r in worker_report["cases"] if r["case_id"] == "OPER-009")
                observed = dict(worker_row.get("observed", {}))
                passed = worker_row["component_status"] == "PASS"
                reason = "SIMULATED_DISABLED_PREFERENCE_ONLY"
            else:
                # One necessary eligibility check: without a subject no candidate.
                # A subject alone is not proof of eligibility or consent.
                specs = _candidate_specs(_event("TRAINING_PLAN_COMPLETED", {}))
                observed = {"candidates_created": len(specs)}
                passed = not specs
                reason = "MISSING_SUBJECT_ONLY"
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "PASS" if passed else "FAIL",
                    "task_status": "UNKNOWN",
                    "reason": reason,
                    "observed": observed,
                }
            )
        except Exception:  # noqa: BLE001 - don't leak error contents
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "ERROR",
                    "task_status": "UNKNOWN",
                    "reason": "PROBE_ERROR",
                }
            )
    return {
        "scope": "case_bound_outreach_component_probes",
        "source": "synthetic_inputs",
        "limitations": [
            "Recipient list dedupe does not test event replay, database idempotency, concurrency or delivery.",
            "The simulated disabled preference is not evidence of actual consent withdrawal or cross-channel suppression.",
            "Missing-subject filtering is only one condition, not full business eligibility or a delivery check.",
            "No case event fixture, real message broker, database, or external notification channel is exercised.",
        ],
        "cases": rows,
    }
