from scripts.gateway_training_write_live_check import (
    validate_created,
    validate_idempotent,
    validate_jti_replay,
)


def test_validate_created_requires_agent_draft_fixture() -> None:
    assert (
        validate_created(
            200,
            {
                "id": "plan-1",
                "status": "DRAFT",
                "source": "AGENT",
                "title": "[GATEWAY_WRITE_FIXTURE] 草案",
            },
        ).passed
        is True
    )
    assert (
        validate_created(
            200,
            {
                "id": "plan-1",
                "status": "PUBLISHED",
                "source": "AGENT",
                "title": "[GATEWAY_WRITE_FIXTURE] 草案",
            },
        ).passed
        is False
    )


def test_validate_idempotent_requires_same_plan_id() -> None:
    assert validate_idempotent(200, {"id": "plan-1"}, "plan-1").passed is True
    assert validate_idempotent(200, {"id": "plan-2"}, "plan-1").passed is False


def test_validate_jti_replay_requires_conflict() -> None:
    assert validate_jti_replay(409, {"code": "CONFLICT"}).passed is True
    assert validate_jti_replay(201, {"id": "plan-2"}).passed is False
