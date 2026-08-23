from scripts.gateway_training_workflow_live_check import (
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
