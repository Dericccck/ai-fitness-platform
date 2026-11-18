import json
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import mysql_booking_observer as observer
from app.evaluation.task_eval.sandbox_preflight import PreflightError

FACTS = {
    "database": "fitness_eval",
    "identity": "root@localhost",
    "contract_count": 1,
    "remaining_hours": 3,
    "appointments": 1,
    "active_appointments": 1,
    "audit_count": 1,
    "audit_actions": ["CREATE_APPOINTMENT"],
    "consumption_count": 1,
    "linked_audit_and_consumption": 1,
    "witness_contract_count": None,
    "witness_remaining_hours": None,
    "witness_appointments": None,
    "global_contracts": 1,
    "global_remaining_hours": 3,
    "global_appointments": 1,
    "global_create_operations": 1,
    "global_reschedule_operations": 0,
    "global_cancel_operations": 0,
    "global_audits": 1,
    "global_consumptions": 1,
    "global_outbox": 1,
}


def _snapshot(**kwargs):
    options = {
        "run_id": "a" * 16,
        "container_id": "b" * 64,
        "organization_id": "test-org",
        "student_id": "test-student",
        "contract_id": "test-contract",
        "request_id": "test-request",
    }
    return observer.snapshot_booking(**(options | kwargs))


def test_snapshot_is_consistent_scoped_and_never_claims_complete_audit(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda _: {})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kw: 3306)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps(FACTS))

    monkeypatch.setattr(observer.subprocess, "run", run)
    result = _snapshot()
    assert result["snapshot_complete"] is True
    assert result["audit_complete"] is False
    assert result["witness_observed"] is False
    assert result["facts"]["remaining_hours"] == 3
    args, kwargs = calls[0]
    assert kwargs["check"] is True
    assert "START TRANSACTION WITH CONSISTENT SNAPSHOT" in args[-1]
    assert "SET TRANSACTION READ ONLY" in args[-1]
    assert "test-org" not in args[-1]
    assert "CONVERT(0x746573742d6f7267 USING utf8mb4)" in args[-1]


def test_witness_scopes_a_second_contract_and_global_facts(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda _: {})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kw: 3306)
    calls = []

    def run(args, **kwargs):
        calls.append(args[-1])
        facts = FACTS | {
            "witness_contract_count": 1,
            "witness_remaining_hours": 5,
            "witness_appointments": 0,
        }
        return SimpleNamespace(stdout=json.dumps(facts))

    monkeypatch.setattr(observer.subprocess, "run", run)
    result = _snapshot(
        witness_organization_id="org-2",
        witness_student_id="student-2",
        witness_contract_id="contract-2",
    )
    assert result["witness_observed"] is True
    assert result["facts"]["witness_remaining_hours"] == 5
    assert "global_outbox" in calls[0]
    assert "CONVERT(0x6f72672d32 USING utf8mb4)" in calls[0]


def test_incomplete_witness_is_rejected_before_inspection(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda _: pytest.fail("must not inspect"))
    with pytest.raises(PreflightError, match="INVALID_WITNESS_REFERENCE"):
        _snapshot(witness_organization_id="org-2")
    with pytest.raises(PreflightError, match="INVALID_WITNESS_REFERENCE"):
        _snapshot(
            witness_organization_id="test-org",
            witness_student_id="student-2",
            witness_contract_id="contract-2",
        )


@pytest.mark.parametrize(
    "key,value", [("global_outbox", -1), ("global_remaining_hours", None), ("global_audits", "1")]
)
def test_invalid_global_observations_are_rejected(monkeypatch, key, value):
    monkeypatch.setattr(observer, "_inspect", lambda _: {})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kw: 3306)
    monkeypatch.setattr(
        observer.subprocess,
        "run",
        lambda *_args, **_kw: SimpleNamespace(stdout=json.dumps(FACTS | {key: value})),
    )
    with pytest.raises(PreflightError, match="INVALID_SNAPSHOT"):
        _snapshot()


@pytest.mark.parametrize("field", ["organization_id", "student_id", "contract_id", "request_id"])
def test_bad_fixture_reference_is_rejected_before_inspection(monkeypatch, field):
    monkeypatch.setattr(observer, "_inspect", lambda _: pytest.fail("must not inspect"))
    with pytest.raises(PreflightError, match="INVALID_FIXTURE_REFERENCE"):
        _snapshot(**{field: "x'; DROP TABLE contract; --"})


def test_wrong_database_identity_is_rejected(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda _: {})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kw: 3306)
    monkeypatch.setattr(
        observer.subprocess,
        "run",
        lambda *_args, **_kw: SimpleNamespace(stdout=json.dumps(FACTS | {"database": "fitness"})),
    )
    with pytest.raises(PreflightError, match="DATABASE_IDENTITY_MISMATCH"):
        _snapshot()


def test_read_created_event_binds_real_outbox_to_booking_request(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda _: {})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kw: 3306)
    calls = []
    event = {
        "eventId": "appointment-created:appt-1",
        "source": "booking",
        "eventType": "APPOINTMENT_CREATED",
        "aggregateId": "appt-1",
        "organizationId": "org-1",
        "contractVersion": 1,
        "aggregateVersion": 1,
        "payload": {"appointmentId": "appt-1", "studentId": "student-1", "coachId": "coach-1"},
    }

    def run(args, **kwargs):
        calls.append(args[-1])
        return SimpleNamespace(
            stdout=json.dumps(
                {"database": "fitness_eval", "identity": "root@localhost", "events": [event]}
            )
        )

    monkeypatch.setattr(observer.subprocess, "run", run)
    actual = observer.read_created_event(
        run_id="a" * 16,
        container_id="b" * 64,
        organization_id="org-1",
        student_id="student-1",
        coach_id="coach-1",
        request_id="req-1",
    )
    assert actual.event_id == "appointment-created:appt-1"
    assert "JOIN agent_booking_operation" in calls[0]
    assert "SET TRANSACTION READ ONLY" in calls[0]
    assert "CONVERT(0x7265712d31 USING utf8mb4)" in calls[0]
    event["payload"]["studentId"] = "other"
    with pytest.raises(PreflightError, match="INVALID_BOOKING_EVENT"):
        observer.read_created_event(
            run_id="a" * 16,
            container_id="b" * 64,
            organization_id="org-1",
            student_id="student-1",
            coach_id="coach-1",
            request_id="req-1",
        )
