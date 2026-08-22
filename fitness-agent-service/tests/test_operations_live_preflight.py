from argparse import Namespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import scripts.operations_live_preflight as preflight
from scripts.operations_live_preflight import (
    _verify_current_user,
    run_preflight,
    validate_agent_readiness,
    validate_live_response,
)


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


@pytest.mark.asyncio
async def test_business_preflight_checks_booking_service_when_url_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Booking/Fitness 联调必须先发现 8083 不可用，不能等 DeepSeek 请求后才报 503。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    probe = AsyncMock(side_effect=lambda client, name, url: preflight.ProbeResult(name, True, url))
    monkeypatch.setattr(preflight, "_get_json", probe)

    results = await run_preflight(
        Namespace(
            agent_url="http://agent.test",
            gateway_url="http://gateway.test",
            booking_url="http://booking.test",
            timeout_seconds=1.0,
        )
    )

    assert [result.name for result in results] == [
        "agent-context",
        "agent-live",
        "agent-ready",
        "gateway-live",
        "booking-live",
    ]
    assert probe.await_count == 4


@pytest.mark.asyncio
async def test_verify_current_user_accepts_existing_gateway_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务联调必须在模型调用前确认 Token subject 可以映射到 MySQL 用户。"""

    monkeypatch.setenv("AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN", "internal-token")
    request = AsyncMock(
        return_value=type(
            "Response",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"id": "student-001", "name": "本地学员"},
            },
        )()
    )
    client = type("Client", (), {"get": request})()

    result = await _verify_current_user(
        client,
        gateway_url="http://gateway.test",
        signed_context="signed-context",
    )

    assert result.passed is True
    assert result.name == "business-user"
    headers = request.await_args.kwargs["headers"]
    assert headers["X-Internal-Service-Token"] == "internal-token"
    assert headers["X-Agent-Context"] == "signed-context"


@pytest.mark.asyncio
async def test_verify_current_user_rejects_missing_business_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN", "internal-token")
    request = AsyncMock(
        return_value=type(
            "Response",
            (),
            {"status_code": 404, "json": lambda self: {"code": "NOT_FOUND"}},
        )()
    )
    client = type("Client", (), {"get": request})()

    result = await _verify_current_user(
        client,
        gateway_url="http://gateway.test",
        signed_context="signed-context",
    )

    assert result.passed is False
    assert "不存在" in result.detail
