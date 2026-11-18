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
    assert result["facts"]["remaining_hours"] == 3
    args, kwargs = calls[0]
    assert kwargs["check"] is True
    assert "START TRANSACTION WITH CONSISTENT SNAPSHOT" in args[-1]
    assert "SET TRANSACTION READ ONLY" in args[-1]
    assert "test-org" not in args[-1]
    assert "CONVERT(0x746573742d6f7267 USING utf8mb4)" in args[-1]


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
