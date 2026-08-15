from datetime import date

from app.agent.operations_tools import operations_prompt_hint, parse_operations_intent


def test_parses_monthly_course_metric() -> None:
    hint = parse_operations_intent(
        "查看本月课程预约量", today=date(2026, 8, 15)
    )

    assert hint is not None
    assert hint.metric == "COURSE_APPOINTMENT_COUNT"
    assert hint.from_date == date(2026, 8, 1)
    assert hint.to_date == date(2026, 8, 15)


def test_parses_recent_status_metric() -> None:
    hint = parse_operations_intent(
        "统计近7天预约状态", today=date(2026, 8, 15)
    )

    assert hint is not None
    assert hint.metric == "APPOINTMENT_STATUS_BREAKDOWN"
    assert hint.from_date == date(2026, 8, 9)


def test_ambiguous_metric_requires_clarification() -> None:
    assert parse_operations_intent("查看本月经营情况", today=date(2026, 8, 15)) is None
    assert "先向用户澄清" in operations_prompt_hint("查看本月经营情况")


def test_cross_metric_request_is_not_guessed() -> None:
    assert parse_operations_intent("查看课程预约和剩余课时", today=date(2026, 8, 15)) is None
