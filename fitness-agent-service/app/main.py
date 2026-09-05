import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

import structlog
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.operations_audit import OperationsAuditRepository
from app.agent.supervisor import Supervisor
from app.agent.training_plan_generation import TrainingPlanGenerationService
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.admin_knowledge import router as admin_knowledge_router
from app.api.routes.admin_notifications import router as admin_notifications_router
from app.api.routes.admin_operations import router as admin_operations_router
from app.api.routes.agent import router as agent_router
from app.api.routes.capabilities import router as capabilities_router
from app.api.routes.confirmations import router as confirmations_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_review import router as knowledge_review_router
from app.api.routes.memories import router as memories_router
from app.api.routes.memory_candidates import router as memory_candidates_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.rag import router as rag_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.metrics import HttpMetrics, MetricsMiddleware
from app.core.telemetry import configure_tracing
from app.evaluation.telemetry import TruLensTelemetry
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.cache import Cache, SessionLockManager
from app.infrastructure.database import CheckpointStore, Database
from app.infrastructure.gateway_client import GatewayClient
from app.infrastructure.jwks import JwksPublicKeyProvider
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.memory.candidate import MemoryCandidateExtractionService
from app.memory.candidate_repository import MemoryCandidateRepository
from app.memory.candidate_service import MemoryCandidateService
from app.memory.candidate_worker import MemoryCandidateExpiryWorker
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.preferences import NotificationPreferenceRepository
from app.notifications.templates import NotificationTemplateRepository
from app.rag.admin_repository import KnowledgeIngestionRepository
from app.rag.admin_service import KnowledgeAdminService
from app.rag.document_quality import DocumentQualityThresholds
from app.rag.formats import DocumentParserRegistry, PdfOcrProvider, PdfPageRoutingPolicy
from app.rag.ingestion import DocumentIngestionService
from app.rag.ocr import HttpPdfOcrProvider
from app.rag.reindex_repository import KnowledgeReindexRepository
from app.rag.reindex_service import KnowledgeReindexService
from app.rag.reindex_worker import KnowledgeReindexWorker
from app.rag.repository import KnowledgeRepository
from app.rag.review import KnowledgeReviewReportBuilder
from app.rag.review_service import KnowledgeReviewService
from app.rag.safety import ClamAvScanner, CompositeDocumentScanner, StructuralDocumentScanner
from app.rag.service import RagService
from app.rag.storage import DocumentStorage, LocalDocumentStorage, S3DocumentStorage
from app.rag.worker import KnowledgeIngestionWorker
from app.session_summary import SessionSummaryRepository, SessionSummaryService

runtime_settings = get_settings()
configure_logging(runtime_settings.log_level)
logger = structlog.get_logger(__name__)
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
    app.state.trulens_telemetry = TruLensTelemetry(settings)

    # app.state 承担轻量依赖容器的职责。数据库、Checkpoint、Redis、Tool Registry 和
    # LangGraph Supervisor 都在这里统一装配，避免业务 Agent 自行读取环境变量或创建连接。
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.cache = Cache(settings.redis_url)
    app.state.checkpoint_store = CheckpointStore(settings)
    app.state.context_verifier = AgentContextVerifier(
        settings.gateway_context_signing_secret,
        max_ttl_seconds=settings.gateway_context_max_ttl_seconds,
        signing_algorithm=settings.gateway_context_signing_algorithm,
        signing_key_id=settings.gateway_context_signing_key_id,
        signing_key_ring=settings.gateway_context_signing_key_ring,
        verification_public_key_ring=settings.gateway_context_verification_public_key_ring,
        jwks_provider=JwksPublicKeyProvider(
            settings.gateway_context_verification_jwks_url,
            cache_ttl_seconds=settings.gateway_context_verification_jwks_cache_seconds,
            timeout_seconds=settings.gateway_context_verification_jwks_timeout_seconds,
        ),
    )
    app.state.models = ModelGateway(
        settings,
        telemetry=app.state.trulens_telemetry,
        metrics=http_metrics,
    )
    app.state.reranker = RerankerClient(settings)
    app.state.knowledge_repository = KnowledgeRepository(app.state.database)
    parser_registry = DocumentParserRegistry(
        max_source_bytes=settings.rag_max_source_bytes,
        pdf_ocr_provider=_build_ocr_provider(settings),
        pdf_page_routing_policy=PdfPageRoutingPolicy(
            min_image_area_ratio=settings.rag_pdf_min_image_area_ratio,
            max_image_page_text_chars=settings.rag_pdf_max_image_page_text_chars,
            max_image_page_text_area_ratio=settings.rag_pdf_max_image_page_text_area_ratio,
            min_ocr_text_chars=settings.rag_pdf_min_ocr_text_chars,
            min_ocr_confidence=settings.rag_pdf_min_ocr_confidence,
        ),
    )
    app.state.rag_service = RagService(
        app.state.knowledge_repository,
        app.state.models,
        app.state.reranker,
        candidate_limit=settings.rag_candidate_limit,
        keyword_candidate_limit=settings.rag_keyword_candidate_limit,
        top_k=settings.rag_top_k,
        prompt_max_total_chars=settings.rag_prompt_max_total_chars,
        prompt_max_evidence_chars=settings.rag_prompt_max_evidence_chars,
        embedding_batch_size=settings.rag_embedding_batch_size,
        embedding_dimensions=settings.embedding_dimensions,
        vector_weight=settings.rag_vector_weight,
        keyword_weight=settings.rag_keyword_weight,
        rrf_k=settings.rag_rrf_k,
        telemetry=app.state.trulens_telemetry,
    )
    app.state.document_ingestion = DocumentIngestionService(
        app.state.knowledge_repository,
        app.state.rag_service,
        max_chunk_chars=settings.rag_chunk_max_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
        parser_registry=parser_registry,
    )
    app.state.knowledge_jobs = KnowledgeIngestionRepository(app.state.database)
    document_storage = _build_document_storage(settings)
    app.state.knowledge_admin = KnowledgeAdminService(
        app.state.knowledge_jobs,
        app.state.knowledge_repository,
        app.state.document_ingestion,
        document_storage,
        parser_registry,
        KnowledgeReviewReportBuilder(
            max_chunk_chars=settings.rag_chunk_max_chars,
            overlap_chars=settings.rag_chunk_overlap_chars,
            thresholds=DocumentQualityThresholds(
                max_noise_rate=settings.rag_quality_max_noise_rate,
                max_fragment_rate=settings.rag_quality_max_fragment_rate,
                max_duplicate_rate=settings.rag_quality_max_duplicate_rate,
                min_parent_integrity=settings.rag_quality_min_parent_integrity,
                min_table_integrity=settings.rag_quality_min_table_integrity,
                max_missing_pages=settings.rag_quality_max_missing_pages,
                max_ocr_required_pages=settings.rag_quality_max_ocr_required_pages,
            ),
        ),
        _build_document_scanner(settings),
        max_source_bytes=settings.rag_max_source_bytes,
        max_attempts=settings.rag_ingestion_max_attempts,
    )
    app.state.knowledge_review = KnowledgeReviewService(
        app.state.knowledge_jobs,
        document_storage,
    )
    app.state.knowledge_worker = KnowledgeIngestionWorker(
        app.state.knowledge_jobs,
        app.state.knowledge_admin,
        batch_size=settings.rag_ingestion_worker_batch_size,
    )
    app.state.knowledge_reindex_jobs = KnowledgeReindexRepository(app.state.database)
    app.state.knowledge_reindex = KnowledgeReindexService(
        app.state.knowledge_reindex_jobs,
        app.state.document_ingestion,
        document_storage,
        max_attempts=settings.rag_ingestion_max_attempts,
        item_batch_size=settings.rag_reindex_worker_batch_size,
    )
    app.state.knowledge_reindex_worker = KnowledgeReindexWorker(
        app.state.knowledge_reindex_jobs,
        app.state.knowledge_reindex,
        batch_size=settings.rag_reindex_worker_batch_size,
    )
    app.state.gateway = GatewayClient(settings)
    # 经营审计属于 Agent 的可追溯事实，不写入 Java/MySQL 业务库。工具注册时
    # 注入同一个 PostgreSQL 仓储，保证管理员查询成功或失败都能形成持久化审计记录。
    app.state.operations_audit = OperationsAuditRepository(app.state.database)
    # Memory 属于 Agent 的长期上下文，不是 Java/MySQL 的健身业务事实。通过独立仓储和
    # Service 装配，后续可以单独增加过期 Worker、审计和数据保留策略。
    app.state.memory_service = MemoryService(
        MemoryRepository(
            app.state.database,
            terminal_retention_days=settings.memory_terminal_retention_days,
        )
    )
    app.state.notification_outbox = NotificationOutboxRepository()
    app.state.notification_templates = NotificationTemplateRepository()
    app.state.notification_preferences = NotificationPreferenceRepository(
        default_timezone=settings.notification_default_timezone
    )
    app.state.memory_candidate_extractor = MemoryCandidateExtractionService(
        app.state.models, metrics=http_metrics
    )
    # Tool Registry 是 Agent 调用业务能力的唯一入口。它在启动期完成固定工具注册，
    # 让后续 Supervisor 只能看到有 Schema、角色元数据和审计边界的工具集合。
    # 生成器与模型、RAG 连接池一起按进程复用，避免每次 Tool Calling 都重新装配服务；
    # 生成器本身不保存用户身份，当前身份仍由 Supervisor 的运行时上下文注入。
    app.state.training_plan_generator = TrainingPlanGenerationService(
        app.state.models,
        app.state.rag_service,
        memory_service=app.state.memory_service,
        max_output_tokens=settings.training_plan_max_output_tokens,
    )
    app.state.tool_registry = build_fitness_tool_registry(
        app.state.gateway,
        plan_generator=app.state.training_plan_generator,
        memory_service=app.state.memory_service,
        operations_audit_repository=app.state.operations_audit,
        operations_rate_limit_cache=app.state.cache,
        operations_rate_limit_requests=settings.operations_rate_limit_requests,
        operations_rate_limit_window_seconds=settings.operations_rate_limit_window_seconds,
        operations_query_timeout_seconds=settings.operations_query_timeout_seconds,
        operations_metrics=http_metrics,
        telemetry=app.state.trulens_telemetry,
    )
    # 确认参数进入 PostgreSQL 前必须经过应用层加密；密钥缺失时拒绝启动，避免形成
    # “看似持久化、实际明文落库”的不安全降级路径。
    app.state.confirmation_cipher = AesGcmPayloadCipher.from_base64(
        settings.confirmation_encryption_key_base64,
        settings.confirmation_encryption_key_version,
    )
    # 候选正文和确认单参数使用同一套 AES-GCM 密钥管理边界，但通过不同 Repository
    # 隔离业务职责。候选只有 PENDING/APPROVED 等状态元数据可被 SQL 判断，value/unit
    # 必须解密后才能交给批准逻辑，避免数据库泄露用户偏好原文。
    app.state.memory_candidate_service = MemoryCandidateService(
        app.state.memory_candidate_extractor,
        MemoryCandidateRepository(
            app.state.database,
            app.state.confirmation_cipher,
            terminal_retention_days=settings.memory_candidate_terminal_retention_days,
        ),
        app.state.memory_service,
        metrics=http_metrics,
    )
    app.state.memory_candidate_expiry_worker = MemoryCandidateExpiryWorker(
        app.state.memory_candidate_service,
        batch_size=settings.memory_candidate_expiry_batch_size,
        metrics=http_metrics,
    )
    app.state.confirmation_token_issuer = ConfirmationTokenIssuer(
        settings.confirmation_signing_secret,
        ttl_seconds=settings.confirmation_token_ttl_seconds,
        signing_algorithm=settings.confirmation_signing_algorithm,
        signing_key_id=settings.confirmation_signing_key_id,
        signing_private_key_pem=settings.confirmation_signing_private_key_pem,
    )
    app.state.confirmation_service = ConfirmationService(
        ConfirmationRepository(app.state.database),
        app.state.tool_registry,
        app.state.gateway,
        app.state.confirmation_cipher,
        app.state.confirmation_token_issuer,
        ttl_seconds=settings.confirmation_ttl_seconds,
    )
    # 短期会话摘要只用于压缩当前 thread 的上下文，不读取或写入长期 Memory，也不
    # 参与权限判断。它复用同一个密钥管理边界，但使用独立表和 thread AAD 做隔离。
    app.state.session_summary_service = SessionSummaryService(
        app.state.models,
        SessionSummaryRepository(app.state.database),
        app.state.confirmation_cipher,
        trigger_messages=settings.session_summary_trigger_messages,
        keep_recent_messages=settings.session_summary_keep_recent_messages,
        max_summary_chars=settings.session_summary_max_chars,
        max_input_chars=settings.session_summary_max_input_chars,
        retention_days=settings.session_summary_retention_days,
        metrics=http_metrics,
    )
    app.state.session_lock = SessionLockManager(
        app.state.cache.client,
        ttl_seconds=settings.session_lock_ttl_seconds,
    )
    try:
        # Checkpointer 在服务启动阶段创建官方表结构；如果数据库不可用则拒绝启动，
        # 避免服务看似在线却丢失会话状态。
        await app.state.checkpoint_store.start()
        if settings.local_model_warmup_enabled:
            # 预热失败或超时时直接拒绝进入 ready，避免实例看似可用、
            # 实际把模型缺失或内存问题暴露给第一个用户。
            warmup_started_at = perf_counter()
            await asyncio.wait_for(
                app.state.models.warmup_local_embedding(),
                timeout=settings.local_model_warmup_timeout_seconds,
            )
            await asyncio.wait_for(
                app.state.reranker.warmup_local_model(),
                timeout=settings.local_model_warmup_timeout_seconds,
            )
            logger.info(
                "local_models_warmed_up",
                duration_ms=round((perf_counter() - warmup_started_at) * 1000, 2),
                embedding_backend=settings.embedding_backend,
                reranker_backend=settings.reranker_backend,
            )
        app.state.supervisor = Supervisor(
            app.state.models,
            app.state.tool_registry,
            max_tool_steps=settings.agent_max_tool_steps,
            checkpointer=app.state.checkpoint_store.saver,
            session_lock=app.state.session_lock,
            rag_service=app.state.rag_service,
            confirmation_service=app.state.confirmation_service,
            memory_candidate_service=app.state.memory_candidate_service,
            session_summary_service=app.state.session_summary_service,
            telemetry=app.state.trulens_telemetry,
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
    title="AI 健身 Agent 服务",
    version="0.1.0",
    docs_url="/docs" if runtime_settings.api_docs_enabled else None,
    redoc_url="/redoc" if runtime_settings.api_docs_enabled else None,
    lifespan=lifespan,
)
app.state.trace_provider = configure_tracing(app, runtime_settings, metrics=http_metrics)
if runtime_settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware, metrics=http_metrics)
app.add_middleware(RequestContextMiddleware, service_name=runtime_settings.service_name)
app.include_router(agent_router)
app.include_router(capabilities_router)
app.include_router(confirmations_router)
app.include_router(memory_candidates_router)
app.include_router(memories_router)
app.include_router(notifications_router)
app.include_router(admin_knowledge_router)
app.include_router(admin_notifications_router)
app.include_router(admin_operations_router)
app.include_router(knowledge_review_router)
app.include_router(rag_router)
app.include_router(health_router)


def _build_document_storage(settings: Settings) -> DocumentStorage:
    """根据配置选择存储实现，不让厂商细节泄露到服务业务层。"""

    if settings.rag_storage_backend == "local":
        return LocalDocumentStorage(settings.rag_staging_dir)
    return S3DocumentStorage(
        endpoint_url=settings.rag_s3_endpoint_url,
        region=settings.rag_s3_region,
        bucket=settings.rag_s3_bucket,
        access_key=settings.rag_s3_access_key,
        secret_key=settings.rag_s3_secret_key,
    )


def _build_ocr_provider(settings: Settings) -> PdfOcrProvider | None:
    """构建配置中的 OCR 边界；生产配置不完整时让服务启动失败。"""

    if settings.rag_ocr_backend == "disabled":
        return None
    return HttpPdfOcrProvider(
        settings.rag_ocr_endpoint_url,
        api_key=settings.rag_ocr_api_key,
        timeout_seconds=settings.rag_ocr_timeout_seconds,
        max_response_bytes=settings.rag_ocr_max_response_bytes,
    )


def _build_document_scanner(settings: Settings) -> CompositeDocumentScanner:
    """将确定性检查与选定的 fail-closed 恶意软件服务组合起来。"""

    malware_scanner = None
    if settings.rag_malware_scanner_backend == "clamav":
        malware_scanner = ClamAvScanner(
            settings.rag_clamav_host,
            port=settings.rag_clamav_port,
            timeout_seconds=settings.rag_clamav_timeout_seconds,
        )
    return CompositeDocumentScanner(StructuralDocumentScanner(), malware_scanner)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """暴露 Prometheus 文本格式指标；生产环境应仅允许监控网络访问。"""

    if not runtime_settings.metrics_enabled:
        return Response(status_code=404)
    return Response(
        content=generate_latest(http_metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
