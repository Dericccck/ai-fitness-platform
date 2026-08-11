import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import Settings

logger = structlog.get_logger(__name__)


def configure_tracing(app: FastAPI, settings: Settings) -> TracerProvider | None:
    """按环境配置启用 OpenTelemetry，并通过 OTLP/HTTP 批量导出 Trace。

    默认配置不会导出任何数据。只有 ``AGENT_OTEL_ENABLED=true`` 且明确提供 Trace
    Endpoint 时才创建 exporter，从而避免开发机或测试环境意外把健身数据元信息发送到
    外部系统。Span 只记录框架产生的请求属性，不在这里写入 Prompt、用户档案或 Tool 参数。
    """

    if not settings.otel_configured:
        logger.info("opentelemetry_disabled")
        return None

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "deployment.environment.name": settings.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        # ParentBased 保留上游采样决定；没有上游上下文时再按本服务采样率决策。
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_traces_endpoint,
        timeout=settings.otel_export_timeout_seconds,
    )
    # BatchSpanProcessor 在后台批量发送，避免每个请求同步等待观测平台网络响应。
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # 健康检查和 Metrics 高频且价值较低，排除后可减少噪声与存储成本。
    excluded_urls = "/health/live,/health/ready,/metrics"
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=excluded_urls,
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    logger.info(
        "opentelemetry_enabled",
        endpoint=settings.otel_exporter_otlp_traces_endpoint,
        sample_ratio=settings.otel_trace_sample_ratio,
    )
    return provider
