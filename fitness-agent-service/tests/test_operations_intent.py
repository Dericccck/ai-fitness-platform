from datetime import date

from app.agent.operations_tools import (
    OperationsMetricToolInput,
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
