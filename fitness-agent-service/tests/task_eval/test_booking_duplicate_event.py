from copy import deepcopy

import pytest

from app.evaluation.task_eval.booking_contracts import matches_frozen_case
from app.evaluation.task_eval.booking_duplicate_event import (
    run_duplicate_event_probe,
    verify_replay,
)
from app.evaluation.task_eval.dataset import load_dataset
from app.proactive.events import ProactiveEventMessage


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
        "facts": {
            "contract_count": 1,
            "remaining_hours": 1,
            "appointments": 1,
            "active_appointments": 1,
            "audit_count": 1,
            "consumption_count": 1,
            "linked_audit_and_consumption": 1,
            "witness_contract_count": 1,
            "witness_remaining_hours": 5,
            "witness_appointments": 0,
            "global_contracts": 2,
            "global_remaining_hours": 6,
            "global_appointments": 1,
            "global_create_operations": 1,
            "global_reschedule_operations": 0,
            "global_cancel_operations": 0,
            "global_audits": 1,
            "global_consumptions": 1,
            "global_outbox": 1,
            "audit_actions": ["CREATE_APPOINTMENT"],
        },
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
        "equal_but_no_booking",
        "equal_but_missing_fact",
        "equal_but_invalid_type",
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
    elif mutation == "equal_but_no_booking":
        value["mysql_before"]["facts"]["appointments"] = 0
        value["mysql_after"]["facts"]["appointments"] = 0
    elif mutation == "equal_but_missing_fact":
        del value["mysql_before"]["facts"]["global_outbox"]
        del value["mysql_after"]["facts"]["global_outbox"]
    elif mutation == "equal_but_invalid_type":
        value["mysql_before"]["facts"]["appointments"] = True
        value["mysql_after"]["facts"]["appointments"] = True
    else:
        value["baseline"]["inbox_total"] = 1
    with pytest.raises(ValueError, match="DUPLICATE_EVENT_EFFECT_MISMATCH"):
        verify_replay(**value)


@pytest.mark.parametrize(
    "mutation", ["event_id", "aggregate_version", "contract_version", "payload"]
)
async def test_unbound_event_rejected_before_database_access(mutation):
    event = ProactiveEventMessage.model_validate(
        {
            "eventId": "appointment-created:appt-1",
            "source": "booking",
            "eventType": "APPOINTMENT_CREATED",
            "aggregateId": "appt-1",
            "organizationId": "org-1",
            "contractVersion": 1,
            "aggregateVersion": 1,
            "payload": {"appointmentId": "appt-1", "studentId": "student-1", "coachId": "coach-1"},
        }
    )
    if mutation == "event_id":
        event.event_id = "appointment-created:other"
    elif mutation == "aggregate_version":
        event.aggregate_version = 2
    elif mutation == "contract_version":
        event.contract_version = 2
    else:
        event.payload["appointmentId"] = "other"

    async def no_snapshot():
        raise AssertionError("should reject before observing or modifying databases")

    with pytest.raises(ValueError, match="INVALID_EVENT_FIXTURE"):
        await run_duplicate_event_probe(database=None, event=event, mysql_snapshot=no_snapshot)
