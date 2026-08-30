from pathlib import Path

import pytest

from scripts.alertmanager_route_check import (
    AlertmanagerRouteCheckError,
    _port_is_available,
    validate_runtime_options,
)


def test_real_alertmanager_config_exists() -> None:
    root = Path(__file__).parents[1]
    config = root.parent / "deployment/observability/alertmanager.yml"

    assert config.is_file()
    content = config.read_text(encoding="utf-8")
    assert "send_resolved: true" in content
    assert 'severity="critical"' in content
    assert "equal: [service]" in content


def test_runtime_options_reject_same_port() -> None:
    with pytest.raises(AlertmanagerRouteCheckError, match="不能相同"):
        validate_runtime_options(18080, 18080, 20.0)


def test_runtime_options_reject_unbounded_timeout() -> None:
    with pytest.raises(AlertmanagerRouteCheckError, match="0-120"):
        validate_runtime_options(18080, 19093, 121.0)


def test_port_probe_returns_boolean() -> None:
    assert isinstance(_port_is_available(0), bool)
