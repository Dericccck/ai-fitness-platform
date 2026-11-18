from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from app.agent import fitness_tools
from app.agent.tool_registry import ToolRegistry
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_cases, booking_local
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


@pytest.mark.parametrize(
    "case_id",
    [
        "BOOK-001",
        "BOOK-002",
        "BOOK-003",
        "BOOK-004",
        "BOOK-005",
        "BOOK-006",
        "BOOK-007",
        "BOOK-010",
    ],
)
async def test_components_never_claim_complete_case(case_id: str) -> None:
    trial = await booking_cases.run(case(case_id))
    assert trial["executed"] and trial["status"] == "INCONCLUSIVE", trial
    assert all(trial["evidence"]["checks"].values())
    assert all(
        a["status"] == "UNKNOWN"
        for a in trial["assertions"]
        if not a["id"].startswith("supplemental_")
    )
    assert "NO_REAL_MODEL_OR_CASE_CONVERSATION_EVALUATED" in trial["limitations"]


@pytest.mark.parametrize(
    "case_id",
    [
        "BOOK-001",
        "BOOK-002",
        "BOOK-003",
        "BOOK-004",
        "BOOK-005",
        "BOOK-006",
        "BOOK-007",
        "BOOK-010",
    ],
)
def test_case_drift_rejects_fixture(case_id: str) -> None:
    for mutate in ("actor", "message", "expected"):
        changed = case(case_id).model_copy(deep=True)
        if mutate == "actor":
            changed.actor.user_ref = "other"
        elif mutate == "message":
            changed.steps[0].message = "different"
        else:
            changed.expected.reference_points = ["different"]
        assert not booking_cases.supports(changed)


async def test_duplicate_booking_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def duplicate(self: Any, context: Any, payload: Any) -> Any:
        result = await original(self, context, payload)
        await original(self, context, payload)
        return result

    monkeypatch.setattr(booking_local.Gateway, "create_booking", duplicate)
    trial = await booking_cases.run(case("BOOK-001"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["one_booking_one_credit"]


@pytest.mark.parametrize("field", ["coachId", "startTime", "endTime"])
async def test_approved_booking_with_wrong_coach_or_time_fails(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    original = booking_local.Gateway.create_booking

    async def alter(self: Any, context: Any, payload: Any) -> Any:
        return await original(self, context, {**payload, field: "wrong-value"})

    monkeypatch.setattr(booking_local.Gateway, "create_booking", alter)
    trial = await booking_cases.run(case("BOOK-002"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["one_exact_coach_and_time"]


@pytest.mark.parametrize(
    ("case_id", "field", "original_value", "failed_check"),
    [
        ("BOOK-003", "course_id", "course-1", "course_change_changes_bound_hash"),
        ("BOOK-005", "coach_id", "coach-1", "coach_change_changes_bound_hash"),
    ],
)
async def test_changed_course_or_coach_cannot_be_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    field: str,
    original_value: str,
    failed_check: str,
) -> None:
    original = ConfirmationService.prepare

    async def force_value(self: Any, **kwargs: Any) -> Any:
        kwargs["raw_input"][field] = original_value
        return await original(self, **kwargs)

    monkeypatch.setattr(ConfirmationService, "prepare", force_value)
    trial = await booking_cases.run(case(case_id))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"][failed_check]


async def test_confirmation_reusing_wrong_contract_is_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ConfirmationService.prepare

    async def force_contract(self: Any, **kwargs: Any) -> Any:
        kwargs["raw_input"]["contract_id"] = "contract-1"
        return await original(self, **kwargs)

    monkeypatch.setattr(ConfirmationService, "prepare", force_contract)
    trial = await booking_cases.run(case("BOOK-004"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["changed_contract_changes_confirmation_hash"]


async def test_student_context_rebinding_regression_is_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ToolRegistry.bind_context_input

    def bind_wrong(self: Any, tool_id: str, raw: Any, identity: Any) -> Any:
        bound = original(self, tool_id, raw, identity)
        bound["student_id"] = "other-student"
        return bound

    monkeypatch.setattr(ToolRegistry, "bind_context_input", bind_wrong)
    trial = await booking_cases.run(case("BOOK-007"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["student_bound_to_signed_actor"]


async def test_wrong_organization_binding_is_detected_without_claiming_store_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = await booking_cases.run(case("BOOK-006"))
    assert clean["status"] == "INCONCLUSIVE"
    assert "STORE_IDENTIFIER_AND_SELECTION_NOT_TESTED" in clean["limitations"]
    original = ToolRegistry.bind_context_input

    def bind_wrong(self: Any, tool_id: str, raw: Any, identity: Any) -> Any:
        bound = original(self, tool_id, raw, identity)
        bound["organization_id"] = "other-org"
        return bound

    monkeypatch.setattr(ToolRegistry, "bind_context_input", bind_wrong)
    trial = await booking_cases.run(case("BOOK-006"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["organization_bound_to_signed_scope"]


async def test_timezone_regression_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fitness_tools, "_normalize_gateway_datetime", lambda value: value.replace(tzinfo=UTC)
    )
    trial = await booking_cases.run(case("BOOK-010"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["timezone_and_calendar_day_preserved"]
