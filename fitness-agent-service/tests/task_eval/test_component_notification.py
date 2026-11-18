"""Evaluation invokes the real policy and detects an unsafe change to its decision."""

import pytest

from app.evaluation.task_eval.component_notification import evaluate_notification_policy
from app.notifications.preferences import (
    NotificationPolicyDecision,
    NotificationPreferenceRepository,
)


@pytest.mark.asyncio
async def test_policy_component_runs_real_cases() -> None:
    report = await evaluate_notification_policy()
    assert report["passed"] is True
    assert len(report["cases"]) == 5
    assert report["scope"] == "notification_preference_policy_component_only"


@pytest.mark.asyncio
async def test_policy_component_detects_bad_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_everything(
        self: object, connection: object, **kwargs: object
    ) -> NotificationPolicyDecision:
        return NotificationPolicyDecision(action="ALLOW")

    monkeypatch.setattr(NotificationPreferenceRepository, "evaluate", allow_everything)
    report = await evaluate_notification_policy()
    assert report["passed"] is False
    assert {row["case_id"] for row in report["cases"] if row["status"] == "FAIL"} >= {
        "disabled",
        "quiet_hours",
        "frequency_limit",
    }


@pytest.mark.asyncio
async def test_policy_error_produces_structured_report(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(
        self: object, connection: object, **kwargs: object
    ) -> NotificationPolicyDecision:
        raise RuntimeError("private details")

    monkeypatch.setattr(NotificationPreferenceRepository, "evaluate", broken)
    report = await evaluate_notification_policy()
    assert report["passed"] is False
    assert all(row["status"] == "ERROR" for row in report["cases"])
    assert "private details" not in str(report)
