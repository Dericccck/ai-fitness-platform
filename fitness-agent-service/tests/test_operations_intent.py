from datetime import date

import pytest

from app.agent.operations_tools import (
    OperationsMetricToolInput,
    OperationsQueryPreparationError,
    build_authorized_operations_tool_input,
    operations_metric_catalog_prompt,
    operations_prompt_hint,
    parse_operations_intent,
    validate_operations_query_policy,
)


def test_parses_monthly_course_metric() -> None:
    hint = parse_operations_intent("查看本月课程预约量", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "COURSE_APPOINTMENT_COUNT"
    assert hint.from_date == date(2026, 8, 1)
    assert hint.to_date == date(2026, 8, 15)


@pytest.mark.parametrize("question", ["2026年8月营收", "查看2026-08收入"])
def test_explicit_calendar_month_is_not_replaced_by_rolling_default(question: str) -> None:
    hint = parse_operations_intent(question, today=date(2026, 11, 17))
    assert hint is not None
    assert (hint.from_date, hint.to_date) == (date(2026, 8, 1), date(2026, 8, 31))


def test_last_week_is_full_previous_calendar_week() -> None:
    hint = parse_operations_intent("查看上周营收", today=date(2026, 11, 17))
    assert hint is not None
    assert (hint.from_date, hint.to_date) == (date(2026, 11, 9), date(2026, 11, 15))


@pytest.mark.parametrize(
    "question",
    [
        "查询指定月份营业收入",
        "查询没有交易的月份的收入",
        "查看2024年收入",
        "查看2026年8月1日收入",
        "查看2026-99-01到2026-99-31的营收",
        "查看2026-08-15到2026-08-01的营收",
        "查看近100天营收",
        "查看2026年8月到2026年9月营收",
        "查看上周和本月营收",
        "查看2026年8月及9月营收",
        "查看上半年营收",
        "查看下半年营收",
        "查看过去一年营收",
        "查看最近一年营收",
        "查看上个月到本月营收",
        "查看2026-08-01至2026-08-15及本月营收",
        "查看本月和上个季度营收",
        "查看2026年8月和去年营收",
        "查看近三天营收",
    ],
)
def test_unsupported_or_incomplete_time_never_falls_back_to_another_period(question: str) -> None:
    assert parse_operations_intent(question, today=date(2026, 11, 17)) is None
    assert "先向用户澄清" in operations_prompt_hint(question)


def test_builds_authorized_tool_input_from_question_and_single_signed_org() -> None:
    query = build_authorized_operations_tool_input(
        "统计 2026-08-01 至 2026-08-15 的营收金额按周趋势",
        allowed_organization_ids=frozenset({"org-1"}),
    )

    assert query == {
        "organization_id": "org-1",
        "metric": "REVENUE_AMOUNT",
        "from": "2026-08-01",
        "to": "2026-08-15",
        "limit": 20,
        "bucket": "WEEK",
        "comparison": "NONE",
    }


def test_authorized_tool_input_rejects_ambiguous_multi_org_scope() -> None:
    with pytest.raises(OperationsQueryPreparationError):
        build_authorized_operations_tool_input(
            "统计 2026-08-01 至 2026-08-15 的营收金额按周趋势",
            allowed_organization_ids=frozenset({"org-1", "org-2"}),
        )


def test_parses_recent_status_metric() -> None:
    hint = parse_operations_intent("统计近7天预约状态", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "APPOINTMENT_STATUS_BREAKDOWN"
    assert hint.from_date == date(2026, 8, 9)


def test_parses_completed_class_metric() -> None:
    hint = parse_operations_intent("查看近30天完课量按周趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "COMPLETED_CLASS_COUNT"
    assert hint.bucket == "WEEK"


def test_parses_new_customer_metric() -> None:
    hint = parse_operations_intent("查看近30天新客量按日趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "NEW_CUSTOMER_COUNT"
    assert hint.bucket == "DAY"


def test_parses_revenue_metric() -> None:
    hint = parse_operations_intent("查看本月营收按周趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "REVENUE_AMOUNT"
    assert hint.bucket == "WEEK"


def test_parses_daily_appointment_trend() -> None:
    hint = parse_operations_intent("查看近30天预约量趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "APPOINTMENT_COUNT"
    assert hint.bucket == "DAY"


def test_parses_daily_course_trend() -> None:
    hint = parse_operations_intent("查看本月课程预约量趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "COURSE_APPOINTMENT_COUNT"
    assert hint.bucket == "DAY"


def test_parses_weekly_coach_trend() -> None:
    hint = parse_operations_intent("查看近30天教练预约量按周趋势", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "COACH_APPOINTMENT_COUNT"
    assert hint.bucket == "WEEK"


def test_parses_previous_period_comparison() -> None:
    hint = parse_operations_intent("查看本月预约量，和上月比变化多少", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.metric == "APPOINTMENT_COUNT"
    assert hint.comparison == "PREVIOUS_PERIOD"
    assert hint.from_date == date(2026, 8, 1)
    assert hint.to_date == date(2026, 8, 15)


def test_parses_year_over_year_comparison() -> None:
    hint = parse_operations_intent("预约量同比变化", today=date(2026, 8, 15))

    assert hint is not None
    assert hint.comparison == "SAME_PERIOD_LAST_YEAR"


def test_mixed_day_and_week_trend_requires_clarification() -> None:
    assert parse_operations_intent("预约量按日和按周趋势", today=date(2026, 8, 15)) is None


def test_ambiguous_metric_requires_clarification() -> None:
    assert parse_operations_intent("查看本月经营情况", today=date(2026, 8, 15)) is None
    assert "先向用户澄清" in operations_prompt_hint("查看本月经营情况")


def test_ambiguous_metric_clarification_lists_fixed_catalog() -> None:
    prompt = operations_prompt_hint("查看本月经营情况")

    assert "预约总量（APPOINTMENT_COUNT）" in prompt
    assert "课程预约量（COURSE_APPOINTMENT_COUNT）" in prompt
    assert "上一自然年同期同比" in prompt
    assert operations_metric_catalog_prompt() in prompt


def test_cross_metric_request_is_not_guessed() -> None:
    assert parse_operations_intent("查看课程预约和剩余课时", today=date(2026, 8, 15)) is None


def test_query_policy_allows_matching_metric_and_range() -> None:
    query = OperationsMetricToolInput(
        organization_id="org-1",
        metric="APPOINTMENT_COUNT",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 15),
    )

    decision = validate_operations_query_policy(
        "查看 2026-08-01 到 2026-08-15 的预约量",
        query,
        today=date(2026, 8, 15),
        allowed_organization_ids=frozenset({"org-1"}),
    )

    assert decision.allowed is True
    assert decision.reason_code == "ALLOWED"


def test_query_policy_rejects_metric_drift_and_expanded_range() -> None:
    query = OperationsMetricToolInput(
        organization_id="org-1",
        metric="COURSE_APPOINTMENT_COUNT",
        from_date=date(2026, 1, 1),
        to_date=date(2026, 3, 31),
    )

    decision = validate_operations_query_policy(
        "查看 2026-08-01 到 2026-08-15 的预约量",
        query,
        today=date(2026, 8, 15),
        allowed_organization_ids=frozenset({"org-1"}),
    )

    assert decision.allowed is False
    assert decision.reason_code == "METRIC_MISMATCH"


def test_query_policy_rejects_unsupported_year_over_year_and_wrong_organization() -> None:
    query = OperationsMetricToolInput(
        organization_id="org-2",
        metric="APPOINTMENT_COUNT",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 15),
    )

    decision = validate_operations_query_policy(
        "查看预约量同比变化",
        query,
        today=date(2026, 8, 15),
        allowed_organization_ids=frozenset({"org-1"}),
    )

    assert decision.allowed is False
    assert decision.reason_code == "ORGANIZATION_SCOPE_MISMATCH"
