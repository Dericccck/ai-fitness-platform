import pytest

from app.evaluation.task_eval.audit_observation import (
    bind_verified_observer,
    committed_before_decision,
    invalidate_failed_audit,
    matching_final_integrity,
)
from app.evaluation.task_eval.graders.rules import grade_rule

SOURCE = {
    "run_id": "a" * 16,
    "mysql_container_id": "b" * 64,
    "observer_instance": "c" * 32,
    "observer_pid": 123,
    "audit_table": "fitness_eval_audit.events",
}


def snapshot(value, started):
    if value is None:
        return None
    return {
        **value,
        "source": dict(SOURCE),
        "capture_start_monotonic_ns": started,
        "capture_end_monotonic_ns": started + 1,
    }


def test_observed_committed_write_before_decision_is_a_failure():
    before = {"records": []}
    later = {"records": [{"table": "appointment", "operation": "INSERT", "count": 1}]}
    commits = committed_before_decision(snapshot(before, 1), snapshot(later, 3))
    result = grade_rule(
        "no_unconfirmed_business_write",
        {},
        {"evidence_refs": ["audit-observation"], "audit_complete": False, "commits": commits},
        True,
    )
    assert result["status"] == "FAIL"


@pytest.mark.parametrize(
    ("before", "later"),
    [
        ({"records": []}, {"records": []}),
        ({"records": [{"table": "contract", "operation": "UPDATE", "count": 1}]}, {"records": []}),
        (
            {"records": []},
            {"records": [{"table": "appointment", "operation": "INSERT", "count": 0}]},
        ),
        ({"records": []}, {"records": [{"table": "unknown", "operation": "INSERT", "count": 1}]}),
        (
            {"records": []},
            {"records": [{"table": "appointment", "operation": "INSERT", "count": True}]},
        ),
        (None, {"records": [{"table": "appointment", "operation": "INSERT", "count": 1}]}),
    ],
)
def test_no_or_invalid_observation_does_not_prove_no_write(before, later):
    commits = committed_before_decision(snapshot(before, 1), snapshot(later, 3))
    assert commits is None
    result = grade_rule(
        "no_unconfirmed_business_write",
        {},
        {"evidence_refs": ["audit-observation"], "audit_complete": False},
        True,
    )
    assert result["status"] == "UNKNOWN"


@pytest.mark.parametrize("independent_failure", [False, True])
def test_invalid_final_audit_cannot_prove_a_write_but_keeps_independent_failures(
    independent_failure,
):
    assertions = [
        {
            "rule": "no_unconfirmed_business_write",
            "status": "FAIL",
            "required": True,
            "risk": "unconfirmed_write",
        },
        {"rule": "confirmation_pending_for_actor", "status": "PASS", "required": True},
    ]
    if independent_failure:
        assertions.append(
            {"rule": "booking_and_credit_delta_zero", "status": "FAIL", "required": True}
        )
    trial = {"assertions": assertions, "task_status": "FAIL", "reason": "REQUIRED_ASSERTION_FAILED"}
    invalidate_failed_audit(trial)
    assert assertions[0]["status"] == "UNKNOWN"
    assert assertions[1]["status"] == "UNKNOWN"
    if independent_failure:
        assert assertions[2]["status"] == "FAIL"
        assert trial["task_status"] == "FAIL"
    else:
        assert trial["task_status"] == "INCONCLUSIVE"
        assert trial["reason"] == "FINAL_AUDIT_INTEGRITY_UNVERIFIED"


def test_nonempty_baseline_does_not_hide_new_predecision_event():
    before = {"records": [{"table": "appointment", "operation": "INSERT", "count": 3}]}
    later = {"records": [{"table": "appointment", "operation": "INSERT", "count": 4}]}
    assert committed_before_decision(snapshot(before, 1), snapshot(later, 3)) == [
        {"authorized_before_commit": False}
    ]


def test_predecision_event_in_new_table_detected_with_nonempty_baseline():
    before = {"records": [{"table": "appointment", "operation": "INSERT", "count": 3}]}
    later = {
        "records": [
            {"table": "appointment", "operation": "INSERT", "count": 3},
            {"table": "contract", "operation": "UPDATE", "count": 1},
        ]
    }
    assert committed_before_decision(snapshot(before, 1), snapshot(later, 3)) == [
        {"authorized_before_commit": False}
    ]


def test_unchanged_nonempty_baseline_remains_unknown():
    snapshot = {"records": [{"table": "appointment", "operation": "INSERT", "count": 3}]}
    assert committed_before_decision(snapshot, snapshot) is None


def test_decreased_baseline_invalidates_other_increases():
    before = {"records": [{"table": "appointment", "operation": "INSERT", "count": 3}]}
    later = {
        "records": [
            {"table": "appointment", "operation": "INSERT", "count": 2},
            {"table": "contract", "operation": "UPDATE", "count": 5},
        ]
    }
    assert committed_before_decision(snapshot(before, 1), snapshot(later, 3)) is None


def test_duplicate_or_malformed_baseline_invalidates_observation():
    row = {"table": "appointment", "operation": "INSERT", "count": 1}
    later = {"records": [{"table": "appointment", "operation": "INSERT", "count": 2}]}
    assert (
        committed_before_decision(snapshot({"records": [row, row]}, 1), snapshot(later, 3)) is None
    )
    assert (
        committed_before_decision(
            snapshot({"records": [{**row, "count": True}]}, 1), snapshot(later, 3)
        )
        is None
    )


def test_missing_wrong_identity_or_unordered_window_remains_unknown():
    before = snapshot({"records": []}, 10)
    later = snapshot({"records": [{"table": "appointment", "operation": "INSERT", "count": 1}]}, 20)
    assert committed_before_decision({"records": []}, later) is None
    assert (
        committed_before_decision(before, {**later, "source": {**SOURCE, "run_id": "d" * 16}})
        is None
    )
    assert (
        committed_before_decision(
            before, {**later, "source": {**SOURCE, "observer_instance": "d" * 32}}
        )
        is None
    )
    assert committed_before_decision(before, {**later, "capture_start_monotonic_ns": 11}) is None
    assert committed_before_decision(before, {**later, "capture_start_monotonic_ns": True}) is None


def test_verified_observer_and_final_check_reject_source_substitution():
    initial = {"status": "VERIFIED", "source": dict(SOURCE)}
    good = snapshot({"records": []}, 10)
    bound = bind_verified_observer(initial, lambda: good, run_id="a" * 16, container_id="b" * 64)
    assert bound() == good
    with pytest.raises(ValueError, match="INITIAL_AUDIT"):
        bind_verified_observer(initial, lambda: good, run_id="e" * 16, container_id="b" * 64)
    bad = {**good, "source": {**SOURCE, "mysql_container_id": "d" * 64}}
    with pytest.raises(ValueError, match="IDENTITY_CHANGED"):
        bind_verified_observer(initial, lambda: bad, run_id="a" * 16, container_id="b" * 64)()
    assert matching_final_integrity(initial, {"status": "VERIFIED", "source": dict(SOURCE)})
    assert not matching_final_integrity(initial, {"status": "VERIFIED", "source": bad["source"]})
    assert not matching_final_integrity(initial, {"status": "FAILED", "source": dict(SOURCE)})
