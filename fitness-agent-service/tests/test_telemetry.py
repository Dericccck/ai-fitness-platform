from fastapi import FastAPI

from app.core.config import Settings
from app.core.telemetry import configure_tracing


def test_tracing_stays_disabled_without_explicit_opt_in() -> None:
    """默认配置不能创建 exporter，防止测试和开发数据被意外发送到外部平台。"""

    settings = Settings(_env_file=None, otel_enabled=False)

    assert configure_tracing(FastAPI(), settings) is None


def test_tracing_requires_both_enable_switch_and_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_traces_endpoint="",
    )

    assert settings.otel_configured is False
