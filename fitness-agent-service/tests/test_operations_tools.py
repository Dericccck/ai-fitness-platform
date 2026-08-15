from datetime import date

import pytest

from app.agent.operations_tools import (
    OperationsMetricToolInput,
    build_operations_report,
    build_operations_tool_result,
)
from app.infrastructure.gateway_client import GatewayOperationsMetric, GatewayOperationsMetricRow


def test_operations_input_only_accepts_metric_catalog() -> None:
    data = OperationsMetricToolInput(
        organization_id="org-1",
        metric="APPOINTMENT_COUNT",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 15),
    )

    assert data.metric == "APPOINTMENT_COUNT"
    assert data.from_date == date(2026, 8, 1)


def test_operations_input_rejects_sql_like_extra_fields() -> None:
    with pytest.raises(ValueError):
        OperationsMetricToolInput(
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            sql="SELECT * FROM appointment",
        )


def test_operations_input_rejects_long_range() -> None:
    with pytest.raises(ValueError):
        OperationsMetricToolInput(
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 4, 10),
        )


def test_operations_report_calculates_summary_without_inventing_trend() -> None:
    result = GatewayOperationsMetric(
        metric="APPOINTMENT_STATUS_BREAKDOWN",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 15),
            "rows": [
                GatewayOperationsMetricRow(dimension="1", label="预约成功", value=8),
                GatewayOperationsMetricRow(dimension="5", label="已取消", value=2),
            ],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    report = build_operations_report(result)

    assert report["total_value"] == 10
    assert report["top_dimension"] == "预约成功"
    assert report["top_share_percent"] == 80.0
    assert report["trend_available"] is False
    assert report["breakdown"] == [
        {"label": "预约成功", "value": 8, "share_percent": 80.0},
        {"label": "已取消", "value": 2, "share_percent": 20.0},
    ]
    assert report["warnings"] == ["结果集中度较高，第一维度占汇总值 80.00%。"]


def test_operations_report_calculates_qualified_daily_trend() -> None:
    result = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        bucket="DAY",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 3),
            "rows": [
                GatewayOperationsMetricRow(dimension="2026-08-01", label="2026-08-01", value=5),
                GatewayOperationsMetricRow(dimension="2026-08-03", label="2026-08-03", value=8),
            ],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    report = build_operations_report(result)

    assert report["trend_available"] is True
    assert report["trend"] == {
        "direction": "UP",
        "first_bucket": "2026-08-01",
        "first_value": 5,
        "last_bucket": "2026-08-03",
        "last_value": 8,
        "delta": 3,
        "change_percent": 60.0,
        "note": "趋势基于首个和末个有记录的时间桶；没有预约的时间桶不会出现在当前结果中。",
    }


def test_operations_report_marks_empty_result_as_non_conclusive() -> None:
    result = GatewayOperationsMetric(
        metric="COURSE_APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 15),
            "rows": [],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    report = build_operations_report(result)

    assert report["total_value"] == 0
    assert report["top_dimension"] is None
    assert report["warnings"] == ["该时间范围没有可统计的有效记录，不能据此判断业务异常。"]


def test_operations_tool_result_keeps_data_and_adds_report() -> None:
    result = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 15),
            "rows": [GatewayOperationsMetricRow(dimension="TOTAL", label="预约总量", value=3)],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    tool_result = build_operations_tool_result(result)

    assert tool_result["data"]["organizationId"] == "org-1"
    assert tool_result["data"]["rows"][0]["value"] == 3
    assert tool_result["report"]["metric_label"] == "预约总量"
