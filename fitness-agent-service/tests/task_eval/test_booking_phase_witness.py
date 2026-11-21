import asyncio
from types import SimpleNamespace

import pytest

from app.confirmation.repository import ConfirmationRepository
from app.evaluation.task_eval import booking_phase_witness as witness
from app.evaluation.task_eval import mysql_restricted_audit as audit
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def record(**changes):
    value = {
        "id": "confirmation",
        "request_id": "request",
        "decision_request_id": "decision",
        "subject_user_id": "student-1",
        "organization_id": "org-1",
        "tool_id": "fitness.booking.create.v1",
        "authorization_status": "APPROVED",
        "approved_at": object(),
    }
    value.update(changes)
    return SimpleNamespace(**value)


async def decide(repo):
    return await repo.decide(
        "confirmation",
        "student-1",
        ("org-1",),
        "APPROVE",
        "decision",
        object(),
        None,
        "student-1",
        ("STUDENT",),
    )


@pytest.mark.asyncio
async def test_phase_fence_occurs_after_repository_decision_and_is_retry_idempotent(monkeypatch):
    calls = []

    async def decided(_self, *_args):
        calls.append("postgres_returned")
        return record()

    def transition():
        calls.append("mysql_fence")
        return {
            "status": "OBSERVED",
            "source": {"run_id": "run"},
            "pending_phase_events": 0,
            "phase_update_committed": True,
            "audit_window_complete": False,
        }

    monkeypatch.setattr(ConfirmationRepository, "decide", decided)
    monkeypatch.setattr(witness, "transition_approved", transition)
    repo = witness.PhaseWitnessRepository(object(), expected_source={"run_id": "run"})
    await asyncio.gather(decide(repo), decide(repo))
    assert calls.count("mysql_fence") == 1
    assert calls.index("postgres_returned") < calls.index("mysql_fence")
    initial = {"status": "VERIFIED", "source": {"run_id": "run"}}
    result = witness.scoped_phase_diagnostic(repo, initial, initial)
    assert result["phase_result"] == "NO_PENDING_PHASE_WRITES"
    assert result["postgres_commit_independently_attested"] is False
    assert result["audit_window_complete"] is False
    assert (
        witness.scoped_phase_diagnostic(repo, initial, {"status": "FAILED"})["status"] == "UNKNOWN"
    )


@pytest.mark.asyncio
async def test_phase_fence_refuses_wrong_identity_and_changed_source(monkeypatch):
    async def decided(_self, *_args):
        return record(organization_id="other")

    monkeypatch.setattr(ConfirmationRepository, "decide", decided)
    monkeypatch.setattr(witness, "transition_approved", lambda: pytest.fail("must not transition"))
    repo = witness.PhaseWitnessRepository(object(), expected_source={"run_id": "run"})
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        await decide(repo)


def test_phase_transition_rejects_missing_or_repeated_state(monkeypatch):
    monkeypatch.setattr(audit, "_container", lambda: ("run", "container"))
    monkeypatch.setattr(audit, "_sql", lambda *_args: "1\n0\n0")
    result = audit.transition_approved()
    assert result["pending_phase_events"] == 0
    assert result["audit_window_complete"] is False
    for invalid in ("0\n0\n0", "1\n-1\n0", "1\n0\n1", "1\n0"):
        monkeypatch.setattr(audit, "_sql", lambda *_args, value=invalid: value)
        with pytest.raises(PreflightError, match="PHASE_TRANSITION_UNVERIFIED"):
            audit.transition_approved()
