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
    if settings.trulens_online_export_enabled:
        _add_trulens_database_exporter(provider, settings)
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


def _add_trulens_database_exporter(provider: TracerProvider, settings: Settings) -> None:
    """把带 TruLens 语义标识的 OTEL Span 直接写入独立评测数据库。

    TruLens 的在线 OTEL 路径和离线 ``TruSession.add_record`` 是两条不同链路：这里
    使用官方 ``TruLensOtelSpanExporter`` 消费同一个 SDK provider，因而真实请求产生的
    record_root/retrieval/generation/tool Span 会落到 ``events`` 表，而不是只停留在
    Collector 的 debug 输出中。导出器依赖缺失或数据库配置错误时启动失败，禁止静默
    降级成“看似已开启但没有数据”的状态。
    """

    try:
        from trulens.core.database.connector.default import DefaultDBConnector
        from trulens.experimental.otel_tracing.core.exporter.connector import (
            TruLensOtelSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover - production image contract
        raise RuntimeError("已启用 TruLens 在线导出，但运行环境没有安装 trulens-core") from exc

    database_url = settings.trulens_database_url.strip()
    if not database_url:
        raise ValueError("已启用 TruLens 在线导出，但 TRULENS_DATABASE_URL 为空")
    try:
        connector = DefaultDBConnector(database_url=database_url)
        exporter = TruLensOtelSpanExporter(connector)
    except Exception as exc:  # pragma: no cover - depends on external database
        raise RuntimeError("无法初始化 TruLens 在线评测数据库连接") from exc
    provider.add_span_processor(BatchSpanProcessor(exporter))
