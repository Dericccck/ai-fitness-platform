from typing import Any

import pytest

from scripts.operations_live_preflight import validate_agent_readiness, validate_live_response


def test_validate_agent_readiness_accepts_ready_dependencies() -> None:
    result = validate_agent_readiness(
        {
            "status": "ready",
            "checks": {
                "database": "ok",
                "checkpoint": "ok",
                "redis": "ok",
                "llm": True,
                "embedding": True,
                "reranker": True,
                "fitness_gateway": True,
            },
        }
    )

    assert result.passed is True


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "not_ready", "checks": {"llm": False}},
        {"status": "ready", "checks": {"database": "failed"}},
        {"status": "ready"},
        None,
    ],
)
def test_validate_agent_readiness_rejects_missing_or_unready_dependencies(
    payload: Any,
) -> None:
    assert validate_agent_readiness(payload).passed is False


def test_validate_live_response_only_accepts_stable_ok_status() -> None:
    assert validate_live_response("gateway-live", {"status": "ok"}).passed is True
    assert validate_live_response("gateway-live", {"status": "down"}).passed is False
