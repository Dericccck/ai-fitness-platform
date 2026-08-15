from datetime import date

import pytest

from app.agent.operations_tools import (
    OperationsMetricToolInput,
    build_operations_comparison_report,
    build_operations_report,
    build_operations_tool_definitions,
    build_operations_tool_result,
)
from app.agent.tool_registry import ToolContext, ToolRegistry
from app.infrastructure.gateway_client import (
    GatewayOperationsMetric,
    GatewayOperationsMetricRow,
    GatewayRequestContext,
)


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


def test_operations_input_rejects_comparison_for_non_total_metric() -> None:
    with pytest.raises(ValueError):
        OperationsMetricToolInput(
            organization_id="org-1",
            metric="COURSE_APPOINTMENT_COUNT",
            comparison="PREVIOUS_PERIOD",
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
        "note": "趋势基于完整时间桶序列；没有预约的时间桶已按 0 计入。",
    }
    assert report["series"] == [
        {"bucket": "2026-08-01", "value": 5},
        {"bucket": "2026-08-02", "value": 0},
        {"bucket": "2026-08-03", "value": 8},
    ]
    assert report["warnings"] == ["已将 1 个无记录时间桶按 0 计入趋势计算。"]


def test_operations_report_fills_week_buckets_and_handles_leading_zero() -> None:
    result = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        bucket="WEEK",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 5),
            "to": date(2026, 8, 19),
            "rows": [
                GatewayOperationsMetricRow(dimension="2026-08-10", label="2026-08-10", value=7),
            ],
            "generatedAt": "2026-08-19T12:00:00Z",
        },
    )

    report = build_operations_report(result)

    assert report["series"] == [
        {"bucket": "2026-08-03", "value": 0},
        {"bucket": "2026-08-10", "value": 7},
        {"bucket": "2026-08-17", "value": 0},
    ]
    assert report["trend_available"] is False
    assert report["trend_note"] == "有效数据桶少于 2 个，暂不判断趋势。"
    assert report["warnings"] == ["已将 2 个无记录时间桶按 0 计入趋势计算。"]


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


def test_operations_comparison_report_calculates_previous_period_delta() -> None:
    current = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 15),
            "rows": [GatewayOperationsMetricRow(dimension="TOTAL", label="预约总量", value=120)],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )
    previous = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 7, 17),
            "to": date(2026, 7, 31),
            "rows": [GatewayOperationsMetricRow(dimension="TOTAL", label="预约总量", value=100)],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    report = build_operations_comparison_report(current, previous)

    assert report["current_total"] == 120
    assert report["previous_total"] == 100
    assert report["delta"] == 20
    assert report["change_percent"] == 20.0
    assert report["direction"] == "UP"


def test_operations_comparison_does_not_divide_by_zero() -> None:
    current = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 8, 1),
            "to": date(2026, 8, 15),
            "rows": [GatewayOperationsMetricRow(dimension="TOTAL", label="预约总量", value=5)],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )
    previous = GatewayOperationsMetric(
        metric="APPOINTMENT_COUNT",
        organizationId="org-1",
        **{
            "from": date(2026, 7, 17),
            "to": date(2026, 7, 31),
            "rows": [],
            "generatedAt": "2026-08-15T12:00:00Z",
        },
    )

    report = build_operations_comparison_report(current, previous)

    assert report["delta"] == 5
    assert report["change_percent"] is None


class FakeOperationsGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[date | None, date | None]] = []

    async def query_operations_metric(
        self, context: GatewayRequestContext, organization_id: str, metric: str, **kwargs: object
    ) -> GatewayOperationsMetric:
        from_date = kwargs.get("from_date")
        to_date = kwargs.get("to_date")
        assert isinstance(from_date, date)
        assert isinstance(to_date, date)
        self.calls.append((from_date, to_date))
        value = 120 if from_date == date(2026, 8, 1) else 100
        return GatewayOperationsMetric(
            metric=metric,
            organizationId=organization_id,
            **{
                "from": from_date,
                "to": to_date,
                "rows": [GatewayOperationsMetricRow(dimension="TOTAL", label="预约总量", value=value)],
                "generatedAt": "2026-08-15T12:00:00Z",
            },
        )


@pytest.mark.asyncio
async def test_operations_tool_queries_current_and_previous_period() -> None:
    gateway = FakeOperationsGateway()
    registry = ToolRegistry()
    for definition in build_operations_tool_definitions(gateway):  # type: ignore[arg-type]
        registry.register(definition)

    result = await registry.invoke(
        "fitness.operations.metric.query.v1",
        {
            "organization_id": "org-1",
            "metric": "APPOINTMENT_COUNT",
            "from": "2026-08-01",
            "to": "2026-08-15",
            "comparison": "PREVIOUS_PERIOD",
        },
        ToolContext(
            gateway_context=GatewayRequestContext(
                signed_context="signed-context", request_id="request-1", trace_id="trace-1"
            )
        ),
    )

    assert gateway.calls == [
        (date(2026, 8, 1), date(2026, 8, 15)),
        (date(2026, 7, 17), date(2026, 7, 31)),
    ]
    assert result["comparison"]["change_percent"] == 20.0


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
