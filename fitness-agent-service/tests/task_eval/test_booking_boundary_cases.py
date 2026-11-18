from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from app.agent import fitness_tools
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_cases
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


@pytest.mark.parametrize("case_id", ["BOOK-013", "BOOK-017"])
async def test_case_observes_specific_boundary_without_claiming_conversation(case_id: str) -> None:
    result = await booking_cases.run(case(case_id))
    assert result["status"] == "INCONCLUSIVE"
    assert result["executed"]
    assert all(result["evidence"]["checks"].values())
    assert result["assertions"][0]["status"] == "UNKNOWN"
    assert "NO_MODEL_OR_CONVERSATION_EXECUTED" in result["limitations"]


@pytest.mark.parametrize("case_id", ["BOOK-013", "BOOK-017"])
def test_fixture_rejects_contract_drift(case_id: str) -> None:
    changed = case(case_id).model_copy(deep=True)
    changed.steps[0].message = "unrelated input"
    assert not booking_cases.supports(changed)
    changed = case(case_id).model_copy(deep=True)
    changed.expected.reference_points = ["different expectation"]
    assert not booking_cases.supports(changed)
    changed = case(case_id).model_copy(deep=True)
    changed.actor.user_ref = "different-user"
    assert not booking_cases.supports(changed)


async def test_automatically_filling_coach_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ConfirmationService.prepare

    async def fill_coach(self: Any, **kwargs: Any) -> Any:
        kwargs["raw_input"]["coach_id"] = "coach-1"
        return await original(self, **kwargs)

    monkeypatch.setattr(ConfirmationService, "prepare", fill_coach)
    result = await booking_cases.run(case("BOOK-013"))
    assert result["status"] == "FAIL"
    assert not result["evidence"]["checks"]["missing_coach_identifier_rejected"]
    assert not result["evidence"]["checks"]["no_confirmation_created"]


async def test_local_time_interpreted_as_utc_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fitness_tools, "_normalize_gateway_datetime", lambda t: t.replace(tzinfo=UTC)
    )
    result = await booking_cases.run(case("BOOK-017"))
    assert result["status"] == "FAIL"
    assert not result["evidence"]["checks"]["local_time_normalized_to_china_timezone"]
