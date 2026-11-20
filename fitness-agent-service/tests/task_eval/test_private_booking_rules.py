"""The clarified one-store/one-private-slot rules require scoped observations."""

from app.evaluation.task_eval.graders.rules import grade_rule

BOOKING = {
    "organizationId": "org-1",
    "studentId": "student-1",
    "contractId": "contract-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2030-01-01T02:00:00Z",
    "endTime": "2030-01-01T03:00:00Z",
}
SLOT = {key: value for key, value in BOOKING.items() if key != "contractId"}
SCOPE = {key: SLOT[key] for key in ("organizationId", "coachId", "startTime", "endTime")}
REFS = {"evidence_refs": ["synthetic-test-observation"]}


def test_store_requires_independent_resolution_and_exact_booking_scope():
    expected = {"booking_store": BOOKING}
    evidence = {
        **REFS,
        "organization_store_mapping_complete": True,
        "store_snapshot_complete": True,
        "selected_store_organization_id": "org-1",
        "store_booking": BOOKING,
    }
    assert grade_rule("booking_organization_store_matches", expected, evidence)["status"] == "PASS"
    assert (
        grade_rule(
            "booking_organization_store_matches",
            expected,
            {**evidence, "organization_store_mapping_complete": False},
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "booking_organization_store_matches",
            expected,
            {**evidence, "selected_store_organization_id": "org-2"},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "booking_organization_store_matches", expected, {**evidence, "store_booking": {}}
        )["status"]
        == "UNKNOWN"
    )


def test_private_slot_requires_empty_before_one_exact_booking_and_settlement():
    expected = {"slot": SLOT}
    observation = {
        **REFS,
        "slot_scope": SCOPE,
        "slot_snapshots_complete": True,
        "slot_settlement_proven": True,
        "slot_before": [],
        "slot_after": [{"id": "appt-1", **SLOT}],
    }
    assert (
        grade_rule("booking_private_slot_consumed_once", expected, observation)["status"] == "PASS"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_settlement_proven": False},
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_scope": {**SCOPE, "coachId": "other"}},
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_before": [{"id": "occupied"}]},
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once", expected, {**observation, "slot_after": []}
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_after": [{"id": "one", **SLOT}, {"id": "two", **SLOT}]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_after": [{"id": "one", **SLOT, "studentId": "other"}]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "booking_private_slot_consumed_once",
            expected,
            {**observation, "slot_after": [{"id": "one"}]},
        )["status"]
        == "UNKNOWN"
    )


def test_private_slot_rejects_invalid_time_and_duplicate_evidence():
    observation = {
        **REFS,
        "slot_scope": SCOPE,
        "slot_snapshots_complete": True,
        "slot_settlement_proven": True,
        "slot_before": [],
        "slot_after": [{"id": "same", **SLOT}, {"id": "same", **SLOT}],
    }
    assert (
        grade_rule("booking_private_slot_consumed_once", {"slot": SLOT}, observation)["status"]
        == "UNKNOWN"
    )
    invalid = {**SLOT, "endTime": SLOT["startTime"]}
    assert (
        grade_rule("booking_private_slot_consumed_once", {"slot": invalid}, observation)["status"]
        == "UNKNOWN"
    )
    invalid = {**SLOT, "startTime": "2030-01-01T02:00:00"}
    assert (
        grade_rule("booking_private_slot_consumed_once", {"slot": invalid}, observation)["status"]
        == "UNKNOWN"
    )
