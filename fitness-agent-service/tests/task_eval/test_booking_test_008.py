"""The test split example is exploratory and never a release result."""

from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_local, booking_test_008
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case() -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == "BOOK-008")


async def test_scripted_two_turn_probe_remains_inconclusive() -> None:
    trial = await booking_cases.run(case())
    assert trial["executed"] is True
    assert trial["status"] == "INCONCLUSIVE"
    assert trial["split"] == "test"
    assert all(trial["evidence"]["checks"].values())
    assert all(
        a["status"] == "UNKNOWN"
        for a in trial["assertions"]
        if not a["id"].startswith("supplemental_")
    )
    assert "SCRIPTED_MODEL" in trial["limitations"]
    assert "NOT_RELEASE_ELIGIBLE" in trial["limitations"]


async def test_wrong_candidate_fails_component_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def wrong(self: Any, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return await original(self, context, {**payload, "courseId": "course-2"})

    monkeypatch.setattr(booking_local.Gateway, "create_booking", wrong)
    trial = await booking_cases.run(case())
    assert trial["status"] == "FAIL"
    assert trial["evidence"]["checks"]["exactly_first_candidate_and_one_credit"] is False


def test_case_and_fixture_changes_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    modified = case().model_copy(deep=True)
    modified.steps[0].message = "我选第二节，请帮我预约"
    assert not booking_test_008.supports(modified)
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(booking_test_008, "_FIXTURE", fixture)
    with pytest.raises(ValueError, match="FIXTURE_CHANGED"):
        booking_test_008.load(case())
