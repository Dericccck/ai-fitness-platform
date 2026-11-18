from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_local
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.graders.rules import grade_rule

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


@pytest.mark.parametrize("case_id", ["BOOK-026", "BOOK-027", "BOOK-028"])
async def test_draft_fault_case_observes_fault_but_never_claims_full_success(case_id: str) -> None:
    trial = await booking_cases.run(case(case_id))
    assertions = {item["id"]: item for item in trial["assertions"]}
    assert trial["executed"] is True
    assert trial["status"] == "INCONCLUSIVE"
    assert assertions["task_result"]["status"] == "UNKNOWN"
    assert assertions["no_write_before"]["status"] == "PASS"
    assert assertions["fault_observed"]["status"] == "PASS"
    assert assertions["supplemental_single_effect"]["status"] == "PASS"


async def test_duplicate_booking_is_reported_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def duplicate(self: Any, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result = await original(self, context, payload)
        await original(self, context, payload)
        return result

    monkeypatch.setattr(booking_local.Gateway, "create_booking", duplicate)
    trial = await booking_cases.run(case("BOOK-026"))
    assert trial["status"] == "FAIL"
    assert trial["evidence"]["booking_count"] == 2


def test_changed_fault_steps_not_silently_mapped_to_different_test() -> None:
    changed = case("BOOK-026").model_copy(deep=True)
    changed.steps[2].fault = "business_timeout"
    assert not booking_cases.supports(changed)


def test_fault_grader_requires_matching_and_complete_evidence() -> None:
    evidence = {
        "evidence_refs": ["test:audit"],
        "fault_injection": {
            "fault": "business_timeout",
            "injected": True,
            "observed": True,
            "observation_complete": True,
        },
    }
    assert grade_rule("fault_observed", {"fault": "business_timeout"}, evidence)["status"] == "PASS"
    assert (
        grade_rule("fault_observed", {"fault": "drop_business_response"}, evidence)["status"]
        == "UNKNOWN"
    )
    evidence["fault_injection"]["observed"] = False
    assert grade_rule("fault_observed", {"fault": "business_timeout"}, evidence)["status"] == "FAIL"


@pytest.mark.parametrize(
    "case_id",
    [
        "BOOK-018",
        "BOOK-026",
        "BOOK-027",
        "BOOK-028",
        "BOOK-032",
        "BOOK-033",
        "BOOK-034",
        "BOOK-035",
    ],
)
def test_fixtures_refuse_actor_trigger_and_assertion_drift(case_id: str) -> None:
    original = case(case_id)
    assert booking_cases.supports(original)
    changed = original.model_copy(deep=True)
    changed.actor.user_ref = "other_user"
    assert not booking_cases.supports(changed)
    changed = original.model_copy(deep=True)
    changed.trigger.type = "event"
    assert not booking_cases.supports(changed)
    changed = original.model_copy(deep=True)
    changed.expected.reference_points = ["不同的预期"]
    assert not booking_cases.supports(changed)
    changed = original.model_copy(deep=True)
    changed.expected.assertions[0].required = False
    assert not booking_cases.supports(changed)


async def test_booking_rejection_probe_preserves_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed(_: str) -> dict[str, Any]:
        return {"status": "ERROR", "checks": {"chat_api_succeeded": False}}

    monkeypatch.setattr(booking_local, "evaluate_case", failed)
    trial = await booking_cases.run(case("BOOK-018"))
    assert trial["status"] == "ERROR"
    assert not trial["executed"]


async def test_booking_rejection_is_component_observation_only() -> None:
    trial = await booking_cases.run(case("BOOK-018"))
    assert trial["status"] == "INCONCLUSIVE"
    assert trial["evidence"]["status"] == "PASS"
    assert trial["evidence"]["business"]["bookings"] == 0
    assert trial["evidence"]["business"]["credit_delta"] == 0
