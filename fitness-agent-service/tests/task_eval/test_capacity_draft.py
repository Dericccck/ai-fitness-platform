import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import capacity_draft
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def get_case(case_id: str) -> Any:
    return next(case for case in load_dataset(DATASET).cases if case.case_id == case_id)


@pytest.mark.parametrize("case_id,response", [("BOOK-009", "BOOKED"), ("BOOK-022", "FULL")])
def test_synthetic_case_observation_is_never_product_pass(case_id: str, response: str) -> None:
    result = capacity_draft.observe(get_case(case_id))
    assert result["status"] == "INCONCLUSIVE"
    assert result["observation_scope"] == "synthetic_sqlite_double_only"
    assert result["response"] == response
    assert result["violations"] == []
    assert result["before"] == result["before_decision"]


def test_last_seat_duplicate_and_full_do_not_consume_extra_credits() -> None:
    double = capacity_draft.SyntheticCapacity(capacity_draft.load(get_case("BOOK-009")))
    try:
        initial = double.snapshot()
        assert double.availability() == "AVAILABLE"
        assert double.reserve("student-a", approved=False) == "CONFIRMATION_REQUIRED"
        assert double.snapshot() == initial
        assert double.reserve("student-a", approved=True) == "BOOKED"
        booked = double.snapshot()
        assert booked == capacity_draft.Snapshot(2, 2, 1)
        assert double.reserve("student-a", approved=True) == "ALREADY_BOOKED"
        assert double.reserve("student-b", approved=True) == "FULL"
        assert double.snapshot() == booked
    finally:
        double.close()


def test_credit_failure_rolls_back_capacity_update() -> None:
    double = capacity_draft.SyntheticCapacity(capacity_draft.load(get_case("BOOK-009")))
    try:
        double.db.execute("UPDATE wallet SET credits = 0")
        double.db.commit()
        before = double.snapshot()
        with pytest.raises(ValueError, match="INSUFFICIENT_CREDITS"):
            double.reserve("student-a", approved=True)
        assert double.snapshot() == before
    finally:
        double.close()


def test_validator_detects_oversell_early_write_and_unwanted_deduction() -> None:
    initial = capacity_draft.Snapshot(1, 3, 0)
    changed = capacity_draft.Snapshot(2, 2, 1)
    assert "write_before_decision" in capacity_draft.validate(
        "BOOK-009", initial, changed, changed, "BOOKED"
    )
    assert "last_seat_delta" in capacity_draft.validate(
        "BOOK-009", initial, initial, capacity_draft.Snapshot(3, 2, 2), "BOOKED"
    )
    full = capacity_draft.Snapshot(2, 3, 0)
    assert "expected_full" in capacity_draft.validate("BOOK-022", full, full, full, "AVAILABLE")
    assert "full_course_changed_state" in capacity_draft.validate(
        "BOOK-022", full, full, capacity_draft.Snapshot(2, 2, 0), "FULL"
    )


def test_changed_case_and_fixture_rejected(monkeypatch: Any, tmp_path: Path) -> None:
    case = get_case("BOOK-009").model_copy(deep=True)
    case.steps[0].message = "different"
    with pytest.raises(ValueError, match="DRAFT_CASE_CHANGED"):
        capacity_draft.load(case)
    case = get_case("BOOK-022")
    fixture = json.loads(capacity_draft._FIXTURE.read_bytes())
    fixture["cases"]["BOOK-022"]["occupied"] = 1
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(capacity_draft, "_FIXTURE", path)
    with pytest.raises(ValueError, match="DRAFT_FIXTURE_CHANGED"):
        capacity_draft.load(case)
    monkeypatch.setattr(capacity_draft, "_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="DRAFT_FIXTURE_INCONSISTENT"):
        capacity_draft.load(case)
