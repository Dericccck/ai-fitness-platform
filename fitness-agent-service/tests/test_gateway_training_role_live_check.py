from scripts.gateway_training_role_live_check import (
    validate_confirmation_required,
    validate_execution_list,
    validate_hidden,
    validate_unauthorized,
    validate_visible,
)


def test_validate_visible_requires_gateway_plan_id_and_status() -> None:
    assert (
        validate_visible(
            "gateway-coach-read-draft",
            200,
            {"id": "draft-1", "status": "DRAFT"},
            "draft-1",
            "DRAFT",
        ).passed
        is True
    )
    assert (
        validate_visible(
            "gateway-coach-read-draft",
            200,
            {"id": "other", "status": "DRAFT"},
            "draft-1",
            "DRAFT",
        ).passed
        is False
    )


def test_validate_hidden_requires_gateway_forbidden_response() -> None:
    assert validate_hidden("gateway-student-hide-draft", 403, {"code": "FORBIDDEN"}).passed is True
    assert validate_hidden("gateway-student-hide-draft", 200, {"id": "draft-1"}).passed is False


def test_validate_unauthorized_requires_gateway_auth_error() -> None:
    assert (
        validate_unauthorized("gateway-internal-auth-denied", 401, {"code": "UNAUTHORIZED"}).passed
        is True
    )
    assert (
        validate_unauthorized("gateway-internal-auth-denied", 403, {"code": "FORBIDDEN"}).passed
        is False
    )


def test_validate_confirmation_required_accepts_gateway_boundary_rejection() -> None:
    assert validate_confirmation_required("missing-confirmation", 400, {}).passed is True
    assert validate_confirmation_required("missing-confirmation", 401, {}).passed is True
    assert validate_confirmation_required("missing-confirmation", 200, {}).passed is False


def test_validate_execution_list_requires_json_array() -> None:
    assert validate_execution_list("published-executions", 200, []).passed is True
    assert validate_execution_list("published-executions", 200, {}).passed is False
    assert validate_execution_list("published-executions", 403, {"code": "FORBIDDEN"}).passed is False
