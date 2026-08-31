from fastapi import FastAPI
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from prometheus_client import CollectorRegistry, generate_latest

from app.core.config import Settings
from app.core.metrics import HttpMetrics
from app.core.telemetry import _MonitoredTruLensExporter, configure_tracing


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


class _FailingExporter(SpanExporter):
    def export(self, spans):
        raise RuntimeError("synthetic exporter failure")

    def shutdown(self) -> None:
        pass


def test_trulens_export_failure_is_visible_in_metrics() -> None:
    metrics = HttpMetrics.create(
        service_name="fitness-agent-service",
        service_version="test",
        environment="test",
        registry=CollectorRegistry(),
    )
    exporter = _MonitoredTruLensExporter(_FailingExporter(), metrics)

    assert exporter.export([]) is SpanExportResult.FAILURE
    exposition = generate_latest(metrics.registry).decode()
    assert 'status="FAILED"' in exposition
