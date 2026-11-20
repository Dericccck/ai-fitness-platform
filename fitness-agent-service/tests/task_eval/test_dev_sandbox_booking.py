from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import dev_sandbox_booking as module


def snapshot_values():
    return {
        "snapshot_complete": True,
        "witness_observed": True,
        "facts": {
            "appointments": 0,
            "remaining_hours": 2,
            "witness_appointments": 0,
            "witness_remaining_hours": 5,
            "global_contracts": 2,
            "global_remaining_hours": 7,
            "global_appointments": 0,
            "global_create_operations": 0,
            "global_reschedule_operations": 0,
            "global_cancel_operations": 0,
            "global_audits": 0,
            "global_consumptions": 0,
            "global_outbox": 0,
            "witness_contract_count": 1,
        },
    }


class FakeAgent:
    def __init__(self):
        self.approved = False
        self.calls = []

    async def chat(self, message):
        self.calls.append(("chat", message))
        return {"status": "CONFIRMATION_REQUIRED", "confirmation_id": "test-confirmation"}

    async def decide(self, confirmation_id, decision):
        self.calls.append(("decision", confirmation_id, decision))
        self.approved = True
        return {"authorization_status": "APPROVED", "execution_status": "SUCCEEDED"}

    async def await_terminal(self, confirmation_id, **kwargs):
        self.calls.append(("await", kwargs))
        return {}


class FakeRepository:
    def __init__(self, agent):
        self.agent = agent
        self.calls = []

    async def get_for_subject(self, cid, subject, orgs):
        self.calls.append((cid, subject, orgs))
        return SimpleNamespace(
            request_id="test-request",
            subject_user_id="student-1",
            tool_id="fitness.booking.create.v1",
            authorization_status="APPROVED" if self.agent.approved else "PENDING",
            execution_status="SUCCEEDED" if self.agent.approved else "NOT_STARTED",
        )


@pytest.mark.asyncio
async def test_dev_trial_steps_refresh_confirmation_and_preserve_unknown():
    case, fixture = module._load()
    agent = FakeAgent()
    repo = FakeRepository(agent)

    def snapshot(request_id):
        data = snapshot_values()
        if agent.approved:
            data["facts"]["appointments"] = 1
            data["facts"]["remaining_hours"] = 1
            for name in (
                "global_appointments",
                "global_create_operations",
                "global_audits",
                "global_consumptions",
                "global_outbox",
            ):
                data["facts"][name] = 1
            data["facts"]["global_remaining_hours"] = 6
        return data

    def details():
        return {
            "snapshot_complete": True,
            "created_bookings": [
                {
                    "courseId": "course-1",
                    "coachId": "coach-1",
                    "studentId": "student-1",
                    "organizationId": "org-1",
                    "contractId": "contract-1",
                    "startTime": "2030-01-07T01:00:00Z",
                    "endTime": "2030-01-07T02:00:00Z",
                }
            ],
        }

    report = await module._drive(case, fixture, agent, repo, snapshot, details)
    assert report["task_status"] == "INCONCLUSIVE"
    assert [item["type"] for item in report["executed_steps"]] == [
        "chat",
        "assert",
        "decision",
        "await_terminal",
        "decision",
        "await_terminal",
        "assert",
    ]
    assert agent.calls[0] == ("chat", case.steps[0].message)
    assert len(repo.calls) == 3
    assert [row["authorization_status"] for row in report["snapshot_checks"]] == [
        "PENDING",
        "APPROVED",
    ]
    assert [row["status"] for row in report["snapshot_checks"]] == ["MATCH", "MATCH"]
    assert [row["status"] for row in report["assertions"]] == ["PASS", "PASS", "UNKNOWN", "PASS"]
    assert report["repeated_decision"]["status"] == "MATCH"
    assert [call for call in agent.calls if call[0] == "decision"] == [
        ("decision", "test-confirmation", "APPROVE"),
        ("decision", "test-confirmation", "APPROVE"),
    ]
    assert all("test-confirmation" not in str(value) for value in report.values())


@pytest.mark.asyncio
async def test_cross_tenant_or_global_change_is_reported_as_mismatch():
    case, fixture = module._load()
    agent = FakeAgent()
    repo = FakeRepository(agent)
    calls = []

    def snapshot(request_id):
        calls.append(request_id)
        data = deepcopy(snapshot_values())
        if len(calls) > 1:
            data["facts"]["witness_remaining_hours"] = 4
        return data

    report = await module._drive(
        case,
        fixture,
        agent,
        repo,
        snapshot,
        lambda: {"snapshot_complete": True, "created_bookings": []},
    )
    assert report["snapshot_checks"][0]["status"] == "MISMATCH"
    assert report["task_status"] == "FAIL"


def test_development_case_is_not_original_planning_dataset():
    case, fixture = module._load()
    assert case.case_id == "DEV-SANDBOX-BOOK-001" and case.review.status == "draft"
    assert case.dataset_version == "dev-sandbox-booking-v1"
    assert all(
        fixture["booking"][name] in case.steps[0].message for name in ("course_id", "coach_id")
    )


@pytest.mark.asyncio
async def test_committed_write_before_decision_fails_and_stops_before_approval():
    case, fixture = module._load()
    agent = FakeAgent()
    repo = FakeRepository(agent)
    from tests.task_eval.test_audit_observation import snapshot

    snapshots = iter(
        [
            snapshot({"records": [], "audit_window_complete": False}, 1),
            snapshot(
                {
                    "records": [{"table": "appointment", "operation": "INSERT", "count": 1}],
                    "audit_window_complete": False,
                },
                3,
            ),
        ]
    )
    report = await module._drive(
        case,
        fixture,
        agent,
        repo,
        lambda _request_id: snapshot_values(),
        lambda: {"snapshot_complete": True, "created_bookings": []},
        lambda: next(snapshots),
    )
    rows = {row["id"]: row for row in report["assertions"]}
    assert rows["no_write_before"]["status"] == "FAIL"
    assert report["task_status"] == "FAIL"
    assert report["reason"] == "REQUIRED_ASSERTION_FAILED"
    assert not any(call[0] == "decision" for call in agent.calls)


@pytest.mark.asyncio
async def test_concurrent_approval_does_not_turn_audit_event_into_proven_preapproval_write():
    case, fixture = module._load()
    agent = FakeAgent()

    class ConcurrentApprovalRepository(FakeRepository):
        async def get_for_subject(self, cid, subject, orgs):
            record = await super().get_for_subject(cid, subject, orgs)
            if len(self.calls) >= 3:
                record.authorization_status = "APPROVED"
            return record

    repo = ConcurrentApprovalRepository(agent)
    audit_calls = 0

    def audit():
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 1:
            return {"records": [], "audit_window_complete": False}
        return {
            "records": [{"table": "appointment", "operation": "INSERT", "count": 1}],
            "audit_window_complete": False,
        }

    report = await module._drive(
        case,
        fixture,
        agent,
        repo,
        lambda _request_id: snapshot_values(),
        lambda: {"snapshot_complete": True, "created_bookings": []},
        audit,
    )
    rows = {row["id"]: row for row in report["assertions"]}
    assert rows["no_write_before"]["status"] == "UNKNOWN"


def test_dev_trial_cli_reports_decidable_failure_as_exit_two(monkeypatch, tmp_path):
    import sys

    async def failed():
        return {"status": "FAIL"}

    monkeypatch.setattr(module, "run", failed)
    monkeypatch.setattr(
        sys, "argv", ["dev_sandbox_booking", "--report-file", str(tmp_path / "out")]
    )
    assert module.main() == 2


@pytest.mark.asyncio
async def test_duplicate_side_effect_is_not_accepted_as_idempotent():
    case, fixture = module._load()
    agent = FakeAgent()
    repo = FakeRepository(agent)
    decision_count = 0

    async def decide(_cid, _decision):
        nonlocal decision_count
        decision_count += 1
        agent.approved = True
        return {"authorization_status": "APPROVED", "execution_status": "SUCCEEDED"}

    agent.decide = decide

    def snapshot(_request_id):
        data = snapshot_values()
        if decision_count:
            data["facts"]["appointments"] = decision_count
            data["facts"]["remaining_hours"] = 2 - decision_count
            data["facts"]["global_appointments"] = decision_count
        return data

    result = await module._drive(
        case,
        fixture,
        agent,
        repo,
        snapshot,
        lambda: {"snapshot_complete": True, "created_bookings": []},
    )
    assert result["repeated_decision"]["status"] == "MISMATCH"
    assert result["assertions"][0]["status"] == "UNKNOWN"
    assert result["task_status"] == "INCONCLUSIVE"


def test_read_only_details_are_scoped_and_reject_bad_results(monkeypatch):
    import json

    from app.evaluation.task_eval import dev_booking_observer as observer
    from app.evaluation.task_eval.sandbox_preflight import PreflightError

    inspected = []
    monkeypatch.setattr(observer, "_inspect", lambda value: {"id": value})
    monkeypatch.setattr(
        observer, "verify_container", lambda info, **kw: inspected.append((info, kw))
    )
    rows = [
        {
            "courseId": "course-1",
            "coachId": "coach-1",
            "studentId": "student-1",
            "organizationId": "org-1",
            "contractId": "contract-1",
            "startTime": "2030-01-07T01:00:00Z",
            "endTime": "2030-01-07T02:00:00Z",
        }
    ]
    result = {"database": "fitness_eval", "identity": "root@localhost", "rows": rows}
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(stdout=json.dumps(result))

    monkeypatch.setattr(observer.subprocess, "run", run)
    kwargs = {
        "run_id": "x",
        "container_id": "y",
        "organization_id": "org-1",
        "student_id": "student-1",
        "contract_id": "contract-1",
    }
    observed = observer.read_booking_details(**kwargs)
    assert inspected and observed["created_bookings"] == rows
    assert "SET TRANSACTION READ ONLY" in commands[0][-1]
    result["rows"][0]["organizationId"] = "other-org"
    with pytest.raises(PreflightError, match="INVALID_BOOKING_DETAILS"):
        observer.read_booking_details(**kwargs)
