from __future__ import annotations

import argparse

import pytest

from scripts.proactive_booking_live_check import (
    ProactiveBookingLiveCheckError,
    build_config,
)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:8090",
        "gateway_url": "http://127.0.0.1:8081",
        "booking_url": "http://127.0.0.1:8083",
        "rabbitmq_url": "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/",
        "message": "请创建本地测试预约",
        "organization_id": "org-1",
        "database_url": "postgresql+asyncpg://user:pass@127.0.0.1/db",
        "timeout_seconds": 90.0,
        "poll_timeout_seconds": 90.0,
        "execute": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_config_normalizes_sqlalchemy_asyncpg_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")

    config = build_config(_args())

    assert config.database_url == "postgresql://user:pass@127.0.0.1/db"
    assert config.execute is False


def test_build_config_requires_signed_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LIVE_AGENT_CONTEXT", raising=False)

    with pytest.raises(ProactiveBookingLiveCheckError, match="AGENT_LIVE_AGENT_CONTEXT"):
        build_config(_args())


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("message", "测试预约请求"),
        ("organization_id", "DEV_AGENT_ORG_ID"),
        ("database_url", "AGENT_DATABASE_URL"),
    ],
)
def test_build_config_requires_business_probe_inputs(
    monkeypatch: pytest.MonkeyPatch, field: str, message: str
) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    kwargs = {field: ""}

    with pytest.raises(ProactiveBookingLiveCheckError, match=message):
        build_config(_args(**kwargs))
