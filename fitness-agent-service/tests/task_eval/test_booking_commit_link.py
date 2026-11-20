from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import booking_commit_link
from app.evaluation.task_eval.audit_observation import invalidate_failed_audit
from app.evaluation.task_eval.booking_commit_link import evaluate_link
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def example():
    record = SimpleNamespace(
        id="confirmation",
        request_id="request",
        subject_user_id="student",
        decision_request_id="decision",
        authorization_status="APPROVED",
        execution_status="SUCCEEDED",
        credential_jti="private-jti",
    )

    def event(name, version):
        return SimpleNamespace(
            event_type=name,
            authorization_version=version,
            confirmation_id="confirmation",
            request_id="request",
            actor_user_id="student",
            decision_request_id="decision",
            authorization_status="APPROVED",
        )

    return record, [event("APPROVED", 2), event("ISSUED", 3), event("EXECUTION_SUCCEEDED", 5)]


def test_matching_records_are_only_diagnostic():
    record, events = example()
    result = evaluate_link(
        record, events, dict.fromkeys(("consumptions", "operations", "audits", "linked"), 1)
    )
    assert result["status"] == "MATCH"
    assert result["cross_database_commit_order_proven"] is False
    assert result["audit_window_complete"] is False
    assert "private-jti" not in str(result)


@pytest.mark.parametrize("key", ["consumptions", "operations", "audits", "linked"])
def test_missing_or_unlinked_mysql_rows_never_match(key):
    record, events = example()
    counts = dict.fromkeys(("consumptions", "operations", "audits", "linked"), 1)
    counts[key] = 0
    assert evaluate_link(record, events, counts)["status"] == "MISMATCH"


def test_wrong_actor_and_event_order_do_not_match():
    record, events = example()
    events[0].actor_user_id = "other"
    counts = dict.fromkeys(("consumptions", "operations", "audits", "linked"), 1)
    assert evaluate_link(record, events, counts)["status"] == "MISMATCH"
    events[0].actor_user_id = "student"
    events[1].authorization_version = 1
    assert evaluate_link(record, events, counts)["status"] == "MISMATCH"


def test_invalid_counts_are_rejected():
    record, events = example()
    with pytest.raises(ValueError):
        evaluate_link(record, events, {"linked": True})


def test_failed_audit_check_does_not_erase_cross_database_mismatch():
    trial = {
        "assertions": [],
        "commit_link": {"status": "MISMATCH"},
        "task_status": "FAIL",
        "reason": "COMMIT_LINK_MISMATCH",
    }
    invalidate_failed_audit(trial)
    assert trial["task_status"] == "FAIL"
    assert trial["reason"] == "COMMIT_LINK_MISMATCH"


def test_mysql_observer_rejects_injected_request_before_container_access(monkeypatch):
    record, _ = example()
    record.request_id = "request'; DROP TABLE appointment; --"
    monkeypatch.setattr(
        booking_commit_link, "_inspect", lambda _: pytest.fail("must reject before inspecting")
    )
    with pytest.raises(PreflightError):
        booking_commit_link.read_commit_link(run_id="run", container_id="container", record=record)


def test_mysql_observer_rejects_untrusted_database_identity(monkeypatch):
    record, _ = example()
    record.organization_id = "org-1"
    record.tool_id = "fitness.booking.create.v1"
    record.action = "CREATE"
    record.payload_hash = "a" * 64
    record.resource_id = None
    monkeypatch.setattr(booking_commit_link, "_inspect", lambda _: {})
    monkeypatch.setattr(booking_commit_link, "verify_container", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        booking_commit_link.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='{"database":"wrong","identity":"root@localhost","consumptions":1,"operations":1,"audits":1,"linked":1}'
        ),
    )
    with pytest.raises(PreflightError):
        booking_commit_link.read_commit_link(run_id="run", container_id="container", record=record)


def test_mysql_query_is_sent_on_stdin_and_results_exclude_private_ids(monkeypatch):
    record, _ = example()
    record.organization_id = "org-1"
    record.tool_id = "fitness.booking.create.v1"
    record.action = "CREATE"
    record.payload_hash = "a" * 64
    record.resource_id = None
    monkeypatch.setattr(booking_commit_link, "_inspect", lambda _: {})
    monkeypatch.setattr(booking_commit_link, "verify_container", lambda *_args, **_kwargs: None)

    def run(args, **kwargs):
        assert "private-jti" not in str(args)
        assert "707269766174652d6a7469" in kwargs["input"]
        assert "-i" in args
        return SimpleNamespace(
            stdout='{"database":"fitness_eval","identity":"root@localhost","consumptions":1,"operations":1,"audits":1,"linked":1}'
        )

    monkeypatch.setattr(booking_commit_link.subprocess, "run", run)
    result = booking_commit_link.read_commit_link(
        run_id="run", container_id="container", record=record
    )
    assert result == dict.fromkeys(("consumptions", "operations", "audits", "linked"), 1)
    assert "private-jti" not in str(result)
