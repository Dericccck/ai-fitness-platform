from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.metrics import HttpMetrics, MetricsMiddleware
from app.core.telemetry import configure_tracing
from app.infrastructure.cache import Cache
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayClient
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient

runtime_settings = get_settings()
configure_logging(runtime_settings.log_level)
http_metrics = HttpMetrics.create(
    service_name=runtime_settings.service_name,
    service_version=runtime_settings.service_version,
    environment=runtime_settings.environment,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """统一管理进程级基础设施对象的创建与释放。

    数据库连接池、Redis 客户端和模型 SDK 客户端都应在一个服务进程内复用，
    不能在每次请求中重复创建。对象放入 ``app.state`` 后，由 API、Agent 图和工具层
    从同一个容器中获取；退出时按照依赖顺序释放资源，
    避免服务重启或测试结束后残留连接。
    """

    settings = get_settings()

    # app.state 目前承担轻量依赖容器的职责。后续接入 Tracing、Tool Registry 和
    # LangGraph Checkpointer 时仍从这里统一装配，避免业务 Agent 自行读取环境变量。
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.cache = Cache(settings.redis_url)
    app.state.models = ModelGateway(settings)
    app.state.reranker = RerankerClient(settings)
    app.state.gateway = GatewayClient(settings)
    try:
        yield
    finally:
        # 即使请求处理出现异常，FastAPI 仍会进入 finally，确保连接被关闭。
        await app.state.models.close()
        await app.state.gateway.close()
        await app.state.cache.close()
        await app.state.database.close()
        if app.state.trace_provider is not None:
            # 关闭时刷新内存中尚未发送的 Span，避免滚动发布丢失尾部 Trace。
            app.state.trace_provider.shutdown()


app = FastAPI(
    title="AI Fitness Agent Service",
    version="0.1.0",
    docs_url="/docs" if runtime_settings.api_docs_enabled else None,
    redoc_url="/redoc" if runtime_settings.api_docs_enabled else None,
    lifespan=lifespan,
)
app.state.trace_provider = configure_tracing(app, runtime_settings)
if runtime_settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware, metrics=http_metrics)
app.add_middleware(RequestContextMiddleware, service_name=runtime_settings.service_name)
app.include_router(health_router)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """暴露 Prometheus 文本格式指标；生产环境应仅允许监控网络访问。"""

    if not runtime_settings.metrics_enabled:
        return Response(status_code=404)
    return Response(
        content=generate_latest(http_metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
