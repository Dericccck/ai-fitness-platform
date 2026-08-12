from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.admin_knowledge import router as admin_knowledge_router
from app.api.routes.agent import router as agent_router
from app.api.routes.health import router as health_router
from app.api.routes.rag import router as rag_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.metrics import HttpMetrics, MetricsMiddleware
from app.core.telemetry import configure_tracing
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.cache import Cache, SessionLockManager
from app.infrastructure.database import CheckpointStore, Database
from app.infrastructure.gateway_client import GatewayClient
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.admin_repository import KnowledgeIngestionRepository
from app.rag.admin_service import KnowledgeAdminService
from app.rag.formats import DocumentParserRegistry
from app.rag.ingestion import DocumentIngestionService
from app.rag.repository import KnowledgeRepository
from app.rag.safety import StructuralDocumentScanner
from app.rag.service import RagService
from app.rag.storage import DocumentStorage, LocalDocumentStorage, S3DocumentStorage
from app.rag.worker import KnowledgeIngestionWorker

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

    # app.state 承担轻量依赖容器的职责。数据库、Checkpoint、Redis、Tool Registry 和
    # LangGraph Supervisor 都在这里统一装配，避免业务 Agent 自行读取环境变量或创建连接。
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.cache = Cache(settings.redis_url)
    app.state.checkpoint_store = CheckpointStore(settings)
    app.state.context_verifier = AgentContextVerifier(
        settings.gateway_context_signing_secret,
        max_ttl_seconds=settings.gateway_context_max_ttl_seconds,
    )
    app.state.models = ModelGateway(settings)
    app.state.reranker = RerankerClient(settings)
    app.state.knowledge_repository = KnowledgeRepository(app.state.database)
    parser_registry = DocumentParserRegistry(max_source_bytes=settings.rag_max_source_bytes)
    app.state.rag_service = RagService(
        app.state.knowledge_repository,
        app.state.models,
        app.state.reranker,
        candidate_limit=settings.rag_candidate_limit,
        keyword_candidate_limit=settings.rag_keyword_candidate_limit,
        top_k=settings.rag_top_k,
        embedding_batch_size=settings.rag_embedding_batch_size,
        embedding_dimensions=settings.embedding_dimensions,
        vector_weight=settings.rag_vector_weight,
        keyword_weight=settings.rag_keyword_weight,
        rrf_k=settings.rag_rrf_k,
    )
    app.state.document_ingestion = DocumentIngestionService(
        app.state.knowledge_repository,
        app.state.rag_service,
        max_chunk_chars=settings.rag_chunk_max_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
        parser_registry=parser_registry,
    )
    app.state.knowledge_jobs = KnowledgeIngestionRepository(app.state.database)
    app.state.knowledge_admin = KnowledgeAdminService(
        app.state.knowledge_jobs,
        app.state.knowledge_repository,
        app.state.document_ingestion,
        _build_document_storage(settings),
        parser_registry,
        StructuralDocumentScanner(),
        max_source_bytes=settings.rag_max_source_bytes,
        max_attempts=settings.rag_ingestion_max_attempts,
    )
    app.state.knowledge_worker = KnowledgeIngestionWorker(
        app.state.knowledge_jobs,
        app.state.knowledge_admin,
        batch_size=settings.rag_ingestion_worker_batch_size,
    )
    app.state.gateway = GatewayClient(settings)
    # Tool Registry 是 Agent 调用业务能力的唯一入口。它在启动期完成固定工具注册，
    # 让后续 Supervisor 只能看到有 Schema、角色元数据和审计边界的工具集合。
    app.state.tool_registry = build_fitness_tool_registry(app.state.gateway)
    app.state.session_lock = SessionLockManager(
        app.state.cache.client,
        ttl_seconds=settings.session_lock_ttl_seconds,
    )
    try:
        # Checkpointer 在服务启动阶段创建官方表结构；如果数据库不可用则拒绝启动，
        # 避免服务看似在线却丢失会话状态。
        await app.state.checkpoint_store.start()
        app.state.supervisor = Supervisor(
            app.state.models,
            app.state.tool_registry,
            max_tool_steps=settings.agent_max_tool_steps,
            checkpointer=app.state.checkpoint_store.saver,
            session_lock=app.state.session_lock,
            rag_service=app.state.rag_service,
        )
        yield
    finally:
        # 即使请求处理出现异常，FastAPI 仍会进入 finally，确保连接被关闭。
        await app.state.models.close()
        await app.state.gateway.close()
        await app.state.checkpoint_store.close()
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
app.include_router(agent_router)
app.include_router(admin_knowledge_router)
app.include_router(rag_router)
app.include_router(health_router)


def _build_document_storage(settings: Settings) -> DocumentStorage:
    """Select storage from configuration without leaking vendor details into the service."""

    if settings.rag_storage_backend == "local":
        return LocalDocumentStorage(settings.rag_staging_dir)
    return S3DocumentStorage(
        endpoint_url=settings.rag_s3_endpoint_url,
        region=settings.rag_s3_region,
        bucket=settings.rag_s3_bucket,
        access_key=settings.rag_s3_access_key,
        secret_key=settings.rag_s3_secret_key,
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """暴露 Prometheus 文本格式指标；生产环境应仅允许监控网络访问。"""

    if not runtime_settings.metrics_enabled:
        return Response(status_code=404)
    return Response(
        content=generate_latest(http_metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
