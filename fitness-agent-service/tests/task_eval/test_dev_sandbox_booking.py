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
        self.calls.append(("decision", decision))
        self.approved = True

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

    report = await module._drive(case, fixture, agent, repo, snapshot)
    assert report["task_status"] == "INCONCLUSIVE"
    assert [item["type"] for item in report["executed_steps"]] == [
        "chat",
        "assert",
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
    assert [row["status"] for row in report["assertions"]] == ["UNKNOWN", "PASS", "UNKNOWN", "PASS"]
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

    report = await module._drive(case, fixture, agent, repo, snapshot)
    assert report["snapshot_checks"][0]["status"] == "MISMATCH"
    assert report["task_status"] == "INCONCLUSIVE"


def test_development_case_is_not_original_planning_dataset():
    case, fixture = module._load()
    assert case.case_id == "DEV-SANDBOX-BOOK-001" and case.review.status == "draft"
    assert case.dataset_version == "dev-sandbox-booking-v1"
    assert all(
        fixture["booking"][name] in case.steps[0].message for name in ("course_id", "coach_id")
    )
