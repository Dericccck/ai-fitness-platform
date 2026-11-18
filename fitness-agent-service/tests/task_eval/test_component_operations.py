"""Component evaluations must detect policy/report regressions and emit errors safely."""

import pytest

from app.agent.operations_tools import OperationsQueryPolicyDecision
from app.evaluation.task_eval import component_operations


def test_operations_component_runs_synthetic_cases() -> None:
    report = component_operations.evaluate_operations_components()
    assert report["passed"] is True
    assert len(report["cases"]) == 11
    assert report["scope"] == "operations_query_policy_and_trend_report_components_only"


def test_detects_policy_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_allow(*args: object, **kwargs: object) -> OperationsQueryPolicyDecision:
        return OperationsQueryPolicyDecision(True, "ALLOWED", "synthetic regression")

    monkeypatch.setattr(component_operations, "validate_operations_query_policy", always_allow)
    report = component_operations.evaluate_operations_components()
    assert report["passed"] is False
    failed = {row["case_id"] for row in report["cases"] if row["status"] == "FAIL"}
    assert {"wrong_organization", "expanded_dates", "missing_metric"} <= failed


def test_detects_report_regression_and_reports_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("private detail")

    monkeypatch.setattr(component_operations, "build_operations_report", broken)
    report = component_operations.evaluate_operations_components()
    assert report["passed"] is False
    assert all(
        row["status"] == "ERROR" for row in report["cases"] if row["component"] == "trend_report"
    )
    assert "private detail" not in str(report)
