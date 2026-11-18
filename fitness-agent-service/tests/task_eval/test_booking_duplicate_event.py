from copy import deepcopy

import pytest

from app.evaluation.task_eval.booking_contracts import matches_frozen_case
from app.evaluation.task_eval.booking_duplicate_event import verify_replay
from app.evaluation.task_eval.dataset import load_dataset


def good():
    baseline = {
        "inbox_total": 0,
        "inbox_processed": 0,
        "outbox_total": 0,
        "pending": 0,
        "recipients": None,
    }
    after = {
        "inbox_total": 1,
        "inbox_processed": 1,
        "outbox_total": 2,
        "pending": 2,
        "recipients": ["coach-1", "student-1"],
    }
    mysql = {
        "snapshot_complete": True,
        "witness_observed": True,
        "facts": {"appointments": 1, "remaining_hours": 1, "witness_remaining_hours": 5},
    }
    return {
        "baseline": baseline,
        "first": deepcopy(after),
        "second": deepcopy(after),
        "first_acks": 1,
        "second_acks": 1,
        "first_rejects": 0,
        "second_rejects": 0,
        "worker_first": (1, 1, 0),
        "worker_second": (0, 0, 0),
        "mysql_before": deepcopy(mysql),
        "mysql_after": deepcopy(mysql),
    }


def test_consistent_duplicate_delivery_effects_accepted():
    verify_replay(**good())


def test_case_contract_drift_is_not_reused():
    from pathlib import Path

    dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    )
    case = next(item for item in dataset.cases if item.case_id == "BOOK-030")
    assert matches_frozen_case(case)
    changed = case.model_copy(deep=True)
    changed.steps[2].fault = "business_timeout"
    assert not matches_frozen_case(changed)


@pytest.mark.parametrize(
    "mutation",
    [
        "first_not_processed",
        "second_claimed",
        "extra_notification",
        "second_no_ack",
        "recharged",
        "other_org_changed",
        "missing_witness",
        "already_dirty",
        "rejected",
        "invalid_count_type",
    ],
)
def test_side_effects_and_missing_evidence_are_rejected(mutation):
    value = good()
    if mutation == "first_not_processed":
        value["worker_first"] = (1, 0, 1)
    elif mutation == "second_claimed":
        value["worker_second"] = (1, 1, 0)
    elif mutation == "extra_notification":
        value["second"]["outbox_total"] += 1
    elif mutation == "second_no_ack":
        value["second_acks"] = 0
    elif mutation == "recharged":
        value["mysql_after"]["facts"]["remaining_hours"] -= 1
    elif mutation == "other_org_changed":
        value["mysql_after"]["facts"]["witness_remaining_hours"] -= 1
    elif mutation == "missing_witness":
        value["mysql_after"]["witness_observed"] = False
    elif mutation == "rejected":
        value["second_rejects"] = 1
    elif mutation == "invalid_count_type":
        value["first"]["inbox_total"] = True
    else:
        value["baseline"]["inbox_total"] = 1
    with pytest.raises(ValueError, match="DUPLICATE_EVENT_EFFECT_MISMATCH"):
        verify_replay(**value)
