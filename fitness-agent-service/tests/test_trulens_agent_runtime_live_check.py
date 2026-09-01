from __future__ import annotations

import pytest

from app.api.routes.health import _redact_observability_config
from scripts.trulens_agent_runtime_live_check import (
    TruLensAgentRuntimeCheckError,
    _database_target,
    _validate_agent_response,
    _validate_config,
)


def _config_summary() -> dict[str, object]:
    return {
        "observability": {
            "otel": {"configured": True, "trace_sample_ratio": 1.0},
            "trulens": {
                "enabled": True,
                "capture_mode": "evaluation",
                "online_export_enabled": True,
                "database": {"scheme": "postgresql", "host": "127.0.0.1"},
            },
        }
    }


def test_validate_config_accepts_online_postgres() -> None:
    result = _validate_config(_config_summary())
    assert result["capture_mode"] == "evaluation"


def test_validate_config_rejects_disabled_online_export() -> None:
    payload = _config_summary()
    payload["observability"]["trulens"]["online_export_enabled"] = False  # type: ignore[index]
    with pytest.raises(TruLensAgentRuntimeCheckError, match="在线数据库导出"):
        _validate_config(payload)


def test_database_target_rejects_non_postgres() -> None:
    with pytest.raises(TruLensAgentRuntimeCheckError, match="只允许使用 PostgreSQL"):
        _database_target("sqlite:///tmp/trulens.sqlite")


def test_validate_agent_response_requires_fitness_completion() -> None:
    _validate_agent_response({"route": "FITNESS_COACHING", "status": "COMPLETED", "answer": "好的"})
    with pytest.raises(TruLensAgentRuntimeCheckError, match="路由"):
        _validate_agent_response({"route": "BOOKING", "status": "COMPLETED", "answer": "好的"})


def test_health_observability_summary_hides_database_credentials() -> None:
    class FakeSettings:
        otel_configured = True
        otel_trace_sample_ratio = 1.0
        trulens_enabled = True
        trulens_capture_mode = "evaluation"
        trulens_online_export_enabled = True
        trulens_database_url = "postgresql+psycopg://user:secret@127.0.0.1:5434/fitness_agent_eval"

    summary = _redact_observability_config(FakeSettings())
    database = summary["trulens"]["database"]  # type: ignore[index]
    assert database == {
        "configured": True,
        "scheme": "postgresql+psycopg",
        "host": "127.0.0.1",
        "port": 5434,
        "database": "fitness_agent_eval",
    }
    assert "secret" not in str(summary)
