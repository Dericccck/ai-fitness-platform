"""Deterministic local evaluation of operations policy and trend-report components.

No Agent, gateway, SQL or database is run. Trend rows are synthetic inputs that
stand in for a gateway response, not proof of aggregation or authorization.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, cast

from app.agent.operations_tools import (
    OperationsMetricToolInput,
    build_operations_report,
    validate_operations_query_policy,
)
from app.infrastructure.gateway_client import GatewayOperationsMetric


def evaluate_operations_components() -> dict[str, Any]:
    """Run literal test expectations against the actual production functions."""
    today = date(2026, 8, 15)
    message = "查看 2026-08-01 到 2026-08-15 的预约量"
    base: dict[str, Any] = {
        "organization_id": "synthetic-org-a",
        "metric": "APPOINTMENT_COUNT",
        "from": "2026-08-01",
        "to": "2026-08-15",
    }
    policy_cases = [
        ("allowed", message, base, {"allowed": True, "reason_code": "ALLOWED"}),
        (
            "wrong_organization",
            message,
            {**base, "organization_id": "synthetic-org-b"},
            {"allowed": False, "reason_code": "ORGANIZATION_SCOPE_MISMATCH"},
        ),
        (
            "wrong_metric",
            message,
            {**base, "metric": "COURSE_APPOINTMENT_COUNT"},
            {"allowed": False, "reason_code": "METRIC_MISMATCH"},
        ),
        (
            "expanded_dates",
            message,
            {**base, "to": "2026-08-31"},
            {"allowed": False, "reason_code": "DATE_RANGE_MISMATCH"},
        ),
        (
            "missing_metric",
            "查看 2026-08-01 到 2026-08-15 的经营情况",
            base,
            {"allowed": False, "reason_code": "INTENT_REQUIRES_CLARIFICATION"},
        ),
        (
            "multiple_metrics",
            "查看 2026-08-01 到 2026-08-15 的预约量和营收",
            base,
            {"allowed": False, "reason_code": "INTENT_REQUIRES_CLARIFICATION"},
        ),
        (
            "wrong_bucket",
            message + "按周趋势",
            {**base, "bucket": "DAY"},
            {"allowed": False, "reason_code": "BUCKET_MISMATCH"},
        ),
        (
            "wrong_comparison",
            message + "和上期相比变化",
            base,
            {"allowed": False, "reason_code": "COMPARISON_MISMATCH"},
        ),
    ]
    results: list[dict[str, Any]] = []
    for case_id, question, query, expected in policy_cases:
        try:
            decision = validate_operations_query_policy(
                question,
                OperationsMetricToolInput.model_validate(query),
                today=today,
                allowed_organization_ids=frozenset({"synthetic-org-a"}),
            )
            observed = {"allowed": decision.allowed, "reason_code": decision.reason_code}
            results.append(
                {
                    "case_id": case_id,
                    "component": "query_policy",
                    "status": "PASS" if observed == expected else "FAIL",
                    "input": {
                        "message": question,
                        "query": query,
                        "allowed_organization_ids": ["synthetic-org-a"],
                        "today": today.isoformat(),
                    },
                    "expected": expected,
                    "observed": observed,
                }
            )
        except Exception:  # noqa: BLE001 - emit a complete report without exposing error details
            results.append(
                {
                    "case_id": case_id,
                    "component": "query_policy",
                    "status": "ERROR",
                    "reason": "COMPONENT_EXCEPTION",
                }
            )

    trend_cases = [
        (
            "missing_middle_bucket",
            [("2026-08-01", 5), ("2026-08-03", 8)],
            {
                "trend_available": True,
                "series": [("2026-08-01", 5), ("2026-08-02", 0), ("2026-08-03", 8)],
                "direction": "UP",
                "change_percent_is_null": False,
            },
        ),
        (
            "empty_rows",
            [],
            {
                "trend_available": False,
                "series": [("2026-08-01", 0), ("2026-08-02", 0), ("2026-08-03", 0)],
                "direction": None,
                "change_percent_is_null": True,
            },
        ),
        (
            "zero_baseline",
            [("2026-08-01", 0), ("2026-08-03", 8)],
            {
                "trend_available": True,
                "series": [("2026-08-01", 0), ("2026-08-02", 0), ("2026-08-03", 8)],
                "direction": "UP",
                "change_percent_is_null": True,
            },
        ),
    ]
    for case_id, rows, expected in trend_cases:
        try:
            metric = GatewayOperationsMetric.model_validate(
                {
                    "metric": "APPOINTMENT_COUNT",
                    "bucket": "DAY",
                    "organizationId": "synthetic-org-a",
                    "from": "2026-08-01",
                    "to": "2026-08-03",
                    "rows": [
                        {"dimension": day, "label": day, "value": value} for day, value in rows
                    ],
                    "generatedAt": datetime(2026, 8, 3, tzinfo=UTC),
                }
            )
            report = cast(dict[str, Any], build_operations_report(metric))
            trend = report.get("trend") or {}
            observed = {
                "trend_available": report["trend_available"],
                "series": [(row["bucket"], row["value"]) for row in report.get("series", [])],
                "direction": trend.get("direction"),
                "change_percent_is_null": trend.get("change_percent") is None,
            }
            results.append(
                {
                    "case_id": case_id,
                    "component": "trend_report",
                    "status": "PASS" if observed == expected else "FAIL",
                    "input_rows": rows,
                    "expected": expected,
                    "observed": observed,
                    "warnings": report.get("warnings", []),
                }
            )
        except Exception:  # noqa: BLE001 - emit a complete report without exposing error details
            results.append(
                {
                    "case_id": case_id,
                    "component": "trend_report",
                    "status": "ERROR",
                    "reason": "COMPONENT_EXCEPTION",
                }
            )
    return {
        "scope": "operations_query_policy_and_trend_report_components_only",
        "source": "synthetic",
        "limitations": [
            "No Agent, gateway, SQL, database, permission enforcement or real aggregation tested."
        ],
        "cases": results,
        "passed": all(item["status"] == "PASS" for item in results),
    }


def main() -> int:
    report = evaluate_operations_components()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
