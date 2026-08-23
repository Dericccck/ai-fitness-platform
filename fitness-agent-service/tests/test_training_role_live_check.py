from scripts.training_role_live_check import (
    validate_health,
    validate_plan_hidden,
    validate_plan_visible,
    validate_student_create_denied,
)


def test_validate_health_accepts_training_service_status() -> None:
    assert validate_health(200, {"status": "UP"}).passed is True
    assert validate_health(200, {"status": "ok"}).passed is False


def test_validate_student_create_denied_requires_forbidden_response() -> None:
    assert validate_student_create_denied(403, {"code": "FORBIDDEN"}).passed is True
    assert validate_student_create_denied(201, {"id": "plan-1"}).passed is False


def test_validate_plan_visible_requires_expected_id_and_status() -> None:
    assert (
        validate_plan_visible(
            "coach-read-draft", 200, {"id": "draft-1", "status": "DRAFT"}, "draft-1", "DRAFT"
        ).passed
        is True
    )
    assert (
        validate_plan_visible(
            "coach-read-draft", 200, {"id": "other", "status": "DRAFT"}, "draft-1", "DRAFT"
        ).passed
        is False
    )


def test_validate_plan_hidden_requires_forbidden_response() -> None:
    assert validate_plan_hidden("student-hide-draft", 403, {"code": "FORBIDDEN"}).passed is True
    assert validate_plan_hidden("student-hide-draft", 200, {"id": "draft-1"}).passed is False
