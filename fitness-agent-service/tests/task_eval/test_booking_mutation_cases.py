from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_mutation_cases
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


@pytest.mark.parametrize("case_id", ["BOOK-032", "BOOK-033", "BOOK-034", "BOOK-035"])
async def test_mutation_protocol_keeps_draft_semantic_result_unknown(case_id: str) -> None:
    selected = case(case_id)
    assert booking_cases.supports(selected)
    trial = await booking_cases.run(selected)
    assertions = {item["id"]: item for item in trial["assertions"]}
    assert trial["status"] == "INCONCLUSIVE"
    assert assertions["task_result"]["status"] == "UNKNOWN"
    assert assertions["no_write_before"]["status"] == "PASS"
    assert all(
        item["status"] == "PASS"
        for item in trial["assertions"]
        if item["id"].startswith("supplemental_")
    )


async def test_extra_credit_refund_is_reported_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_mutation_cases.Gateway.cancel_booking

    async def double_refund(self: Any, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result = await original(self, context, payload)
        self.db.execute("UPDATE credits SET remaining = remaining + 1")
        return result

    monkeypatch.setattr(booking_mutation_cases.Gateway, "cancel_booking", double_refund)
    trial = await booking_cases.run(case("BOOK-034"))
    assert trial["status"] == "FAIL"
    assert trial["evidence"]["after"]["credits"] == 12


def test_changed_message_or_decision_is_not_silently_mapped() -> None:
    changed = case("BOOK-032").model_copy(deep=True)
    changed.steps[0].message = "取消预约"
    assert not booking_cases.supports(changed)
    changed = case("BOOK-035").model_copy(deep=True)
    changed.steps[2].decision = "APPROVE"
    assert not booking_cases.supports(changed)
