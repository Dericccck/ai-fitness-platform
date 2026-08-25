from argparse import Namespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import scripts.customer_service_live_preflight as preflight
from scripts.customer_service_live_preflight import (
    CustomerServicePreflightError,
    run_preflight,
    validate_agent_readiness,
    validate_live_response,
)


def test_customer_service_preflight_accepts_ready_agent() -> None:
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
        {"status": "not_ready", "checks": {"database": "ok"}},
        {"status": "ready", "checks": {"database": "failed"}},
        {"status": "ready"},
        None,
    ],
)
def test_customer_service_preflight_rejects_unready_agent(payload: Any) -> None:
    assert validate_agent_readiness(payload).passed is False


def test_customer_service_preflight_accepts_only_ok_live_status() -> None:
    assert validate_live_response("customer-service-live", {"status": "ok"}).passed is True
    assert validate_live_response("customer-service-live", {"status": "down"}).passed is False


@pytest.mark.asyncio
async def test_preflight_checks_customer_service_without_business_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    probe = AsyncMock(
        side_effect=lambda client, name, url: preflight.ProbeResult(name, True, url)
    )
    monkeypatch.setattr(preflight, "_get_json", probe)

    results = await run_preflight(
        Namespace(
            agent_url="http://agent.test",
            gateway_url="http://gateway.test",
            customer_service_url="http://customer.test",
            timeout_seconds=1.0,
        )
    )

    assert [result.name for result in results] == [
        "agent-context",
        "agent-live",
        "agent-ready",
        "gateway-live",
        "customer-service-live",
    ]
    assert probe.await_count == 4


@pytest.mark.asyncio
async def test_preflight_rejects_non_positive_timeout() -> None:
    with pytest.raises(CustomerServicePreflightError, match="必须大于 0"):
        await run_preflight(
            Namespace(
                agent_url="http://agent.test",
                gateway_url="http://gateway.test",
                customer_service_url="http://customer.test",
                timeout_seconds=0,
            )
        )
