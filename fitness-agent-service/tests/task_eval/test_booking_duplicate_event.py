from copy import deepcopy

import pytest

from app.evaluation.task_eval.booking_contracts import matches_frozen_case
from app.evaluation.task_eval.booking_duplicate_event import (
    _confirmed_java_publisher,
    run_duplicate_event_probe,
    verify_delivery,
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


@pytest.mark.parametrize(
    "mutation",
    [
        "pending",
        "unpublished",
        "claimed",
        "error",
        "retry",
        "bool_attempt",
        "integer_flag",
        "extra",
    ],
)
def test_java_publisher_confirmation_requires_exact_typed_evidence(mutation):
    value = {
        "status": "PUBLISHED",
        "attempt_count": 0,
        "published": True,
        "unclaimed": True,
        "error_free": True,
    }
    assert _confirmed_java_publisher(value)
    if mutation == "pending":
        value["status"] = "PENDING"
    elif mutation == "unpublished":
        value["published"] = False
    elif mutation == "claimed":
        value["unclaimed"] = False
    elif mutation == "error":
        value["error_free"] = False
    elif mutation == "retry":
        value["attempt_count"] = 1
    elif mutation == "bool_attempt":
        value["attempt_count"] = False
    elif mutation == "integer_flag":
        value["published"] = 1
    else:
        value["other"] = "value"
    assert not _confirmed_java_publisher(value)


def good_delivery():
    empty = {
        "inbox_total": 0,
        "attempts_total": 0,
        "published": 0,
        "recipients": None,
        "matched_delivery": 0,
    }
    delivered = {
        "inbox_total": 2,
        "attempts_total": 2,
        "published": 2,
        "recipients": ["coach-1", "student-1"],
        "matched_delivery": 2,
    }
    return {
        "baseline": empty,
        "delivered": deepcopy(delivered),
        "replayed": deepcopy(delivered),
        "first_run": (2, 2, 0, 0, 0),
        "second_run": (0, 0, 0, 0, 0),
        "replay_ack": 1,
        "replay_reject": 0,
        "replay_worker": (0, 0, 0),
        "replay_event_snapshot": {
            "inbox_total": 1,
            "inbox_processed": 1,
            "outbox_total": 2,
            "pending": 0,
            "recipients": ["coach-1", "student-1"],
        },
    }


def test_real_notification_receipts_can_be_verified():
    verify_delivery(**good_delivery())


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty_baseline",
        "missing_receipt",
        "wrong_recipient",
        "duplicate_receipt",
        "missing_join",
        "retry",
        "suppressed",
        "delivery_again",
        "rejected_replay",
        "missing_ack",
        "worker_reprocess",
        "extra_event",
        "bool_count",
    ],
)
def test_notification_delivery_rejects_incomplete_or_wrong_evidence(mutation):
    value = good_delivery()
    if mutation == "dirty_baseline":
        value["baseline"]["attempts_total"] = 1
    elif mutation == "missing_receipt":
        value["delivered"]["attempts_total"] = 1
    elif mutation == "wrong_recipient":
        value["delivered"]["recipients"] = ["other", "student-1"]
    elif mutation == "duplicate_receipt":
        value["replayed"]["inbox_total"] = 3
    elif mutation == "missing_join":
        value["delivered"]["matched_delivery"] = 1
    elif mutation == "retry":
        value["first_run"] = (2, 1, 1, 0, 0)
    elif mutation == "suppressed":
        value["first_run"] = (2, 1, 0, 0, 1)
    elif mutation == "delivery_again":
        value["second_run"] = (1, 1, 0, 0, 0)
    elif mutation == "rejected_replay":
        value["replay_reject"] = 1
    elif mutation == "missing_ack":
        value["replay_ack"] = 0
    elif mutation == "worker_reprocess":
        value["replay_worker"] = (1, 1, 0)
    elif mutation == "extra_event":
        value["replay_event_snapshot"]["inbox_total"] = 2
    else:
        value["baseline"]["inbox_total"] = False
    with pytest.raises(ValueError, match="NOTIFICATION_DELIVERY_MISMATCH"):
        verify_delivery(**value)


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
