from scripts.training_role_live_check import validate_health, validate_student_create_denied


def test_validate_health_accepts_training_service_status() -> None:
    assert validate_health(200, {"status": "UP"}).passed is True
    assert validate_health(200, {"status": "ok"}).passed is False


def test_validate_student_create_denied_requires_forbidden_response() -> None:
    assert validate_student_create_denied(403, {"code": "FORBIDDEN"}).passed is True
    assert validate_student_create_denied(201, {"id": "plan-1"}).passed is False
