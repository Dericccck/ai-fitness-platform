from scripts.gateway_training_workflow_live_check import (
    ProactiveEventObservation,
    validate_proactive_event,
    validate_student_hidden,
    validate_transition,
    validate_visible,
)


def test_validate_transition_requires_target_plan_and_status() -> None:
    assert validate_transition(
        "publish", 200, {"id": "plan-1", "status": "PUBLISHED"}, "plan-1", "PUBLISHED"
    ).passed
    assert not validate_transition(
        "publish", 200, {"id": "plan-2", "status": "PUBLISHED"}, "plan-1", "PUBLISHED"
    ).passed
    assert not validate_transition(
        "publish", 200, {"id": "plan-1", "status": "APPROVED"}, "plan-1", "PUBLISHED"
    ).passed


def test_validate_student_hidden_requires_forbidden_error() -> None:
    assert validate_student_hidden("student-before-publish", 403, {"code": "FORBIDDEN"}).passed
    assert not validate_student_hidden("student-before-publish", 200, {}).passed


def test_validate_visible_requires_published_plan() -> None:
    assert validate_visible(
        "student-after-publish", 200, {"id": "plan-1", "status": "PUBLISHED"}, "plan-1"
    ).passed
    assert not validate_visible(
        "student-after-publish", 200, {"id": "plan-1", "status": "APPROVED"}, "plan-1"
    ).passed


def test_validate_proactive_event_requires_exactly_one_published_in_app_notification() -> None:
    assert validate_proactive_event(
        ProactiveEventObservation(
            "event-1",
            "TRAINING_PLAN_PUBLISHED",
            "PROCESSED",
            1,
            1,
            1,
            ("student-1",),
            ("student-1",),
        ),
        "student-1",
    ).passed
    assert not validate_proactive_event(
        ProactiveEventObservation(
            "event-1", "TRAINING_PLAN_PUBLISHED", "PENDING", 1, 0, 0, ("student-1",), ()
        ),
        "student-1",
    ).passed
    assert not validate_proactive_event(
        ProactiveEventObservation(
            "event-1",
            "TRAINING_PLAN_PUBLISHED",
            "PROCESSED",
            2,
            2,
            2,
            ("student-1",),
            ("student-1",),
        ),
        "student-1",
    ).passed
