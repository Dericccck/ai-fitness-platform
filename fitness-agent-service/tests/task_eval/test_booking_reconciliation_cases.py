from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_reconciliation_cases
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


@pytest.mark.parametrize(
    ("case_id", "expected_status", "reconciled"),
    [("BOOK-029", "SUCCEEDED", 1), ("BOOK-031", "UNKNOWN", 0)],
)
async def test_worker_reconciliation_observes_case_fault(
    case_id: str, expected_status: str, reconciled: int
) -> None:
    trial = await booking_cases.run(case(case_id))
    assert trial["status"] == "INCONCLUSIVE", trial
    assert trial["executed"]
    assertions = {a["id"]: a["status"] for a in trial["assertions"]}
    assert assertions["task_result"] == "UNKNOWN"
    assert assertions["no_write_before"] == "PASS"
    assert assertions["fault_observed"] == "PASS"
    assert all(trial["evidence"]["checks"].values())
    assert trial["evidence"]["before_worker_execution_status"] == "UNKNOWN"
    assert trial["evidence"]["final_execution_status"] == expected_status
    assert trial["evidence"]["worker_reconciled"] == reconciled
    assert trial["evidence"]["gateway_attempts"] == 1


@pytest.mark.parametrize("case_id", ["BOOK-029", "BOOK-031"])
def test_changed_case_refuses_probe(case_id: str) -> None:
    changed = case(case_id).model_copy(deep=True)
    changed.actor.user_ref = "other-user"
    assert not booking_cases.supports(changed)
    changed = case(case_id).model_copy(deep=True)
    changed.steps[2].fault = "different_fault"
    assert not booking_cases.supports(changed)


async def test_unbound_reconciliation_does_not_claim_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = booking_reconciliation_cases.Gateway.reconcile_tool_operation

    async def mismatch(self: Any, **kwargs: Any) -> Any:
        result = await original(self, **kwargs)
        return result.model_copy(update={"organization_id": "other-org"})

    monkeypatch.setattr(booking_reconciliation_cases.Gateway, "reconcile_tool_operation", mismatch)
    trial = await booking_cases.run(case("BOOK-029"))
    assert trial["status"] == "FAIL"
    assert trial["evidence"]["final_execution_status"] == "UNKNOWN"
    assert trial["evidence"]["worker_query_failures"] == 1
