"""Worker smoke checks exercise production orchestration with labeled fake boundaries."""

import pytest

from app.evaluation.task_eval.component_notification_worker import evaluate_notification_worker
from app.notifications.preferences import (
    NotificationPolicyDecision,
    NotificationPreferenceRepository,
)


@pytest.mark.asyncio
async def test_worker_components_do_not_claim_full_task_results():
    report = await evaluate_notification_worker()
    assert report["passed"] is True
    assert {row["case_id"] for row in report["cases"]} == {
        "OPER-009",
        "OPER-010",
        "OPER-011",
        "OPER-012",
        "OPER-013",
    }
    assert all(row["task_status"] == "UNKNOWN" for row in report["cases"])


@pytest.mark.asyncio
async def test_worker_component_detects_unsafe_policy_bypass(monkeypatch):
    async def always_allow(self, connection, *, record, now=None):
        return NotificationPolicyDecision(action="ALLOW")

    monkeypatch.setattr(NotificationPreferenceRepository, "evaluate", always_allow)
    report = await evaluate_notification_worker()
    failures = {row["case_id"] for row in report["cases"] if row["component_status"] == "FAIL"}
    assert failures >= {"OPER-009", "OPER-010", "OPER-011"}
    assert report["passed"] is False
