import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import cast

import structlog
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.movement_analysis import MovementAnalysisService
from app.agent.operations_audit import OperationsAuditRepository
from app.agent.routing import HybridIntentRouter
from app.agent.supervisor import Supervisor
from app.agent.training_plan_generation import (
    GatewayStudentTrainingContextReader,
    TrainingPlanGenerationService,
)
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.admin_decisions import router as admin_decisions_router
from app.api.routes.admin_knowledge import router as admin_knowledge_router
from app.api.routes.admin_notifications import router as admin_notifications_router
from app.api.routes.admin_operations import router as admin_operations_router
from app.api.routes.admin_proactive import router as admin_proactive_router
from app.api.routes.agent import router as agent_router
from app.api.routes.auth import BrowserSessionMiddleware, auth_error
from app.api.routes.auth import router as auth_router
from app.api.routes.capabilities import router as capabilities_router
from app.api.routes.confirmations import router as confirmations_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_review import router as knowledge_review_router
from app.api.routes.memories import router as memories_router
from app.api.routes.memory_candidates import router as memory_candidates_router
from app.api.routes.mobile_auth import router as mobile_auth_router
from app.api.routes.mobile_media import router as mobile_media_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.rag import router as rag_router
from app.api.routes.voice import router as voice_router
from app.api.routes.voice import voice_error
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.reconciliation_worker import ConfirmationReconciliationWorker
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.mcp_metrics import McpRuntimeMetrics
from app.core.metrics import HttpMetrics, MetricsMiddleware
from app.core.multimodal_metrics import MultimodalMetrics
from app.core.telemetry import configure_tracing
from app.decision.repository import DecisionRepository
from app.decision.service import DecisionPlatformService
from app.evaluation.telemetry import TruLensTelemetry
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService
from app.infrastructure.business_tools_client import RoutedBusinessToolsClient
from app.infrastructure.cache import Cache, SessionLockManager
from app.infrastructure.database import CheckpointStore, Database
from app.infrastructure.gateway_client import GatewayClient
from app.infrastructure.jwks import JwksPublicKeyProvider
from app.infrastructure.mcp_client import McpBusinessToolsClient
from app.infrastructure.mobile_auth import MobileAuthService
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.model_protection import RedisDailyModelBudget, RedisModelLease
from app.infrastructure.model_usage import ModelUsageRepository
from app.infrastructure.multimodal_gateway import MultimodalGateway
from app.infrastructure.reranker import RerankerClient
from app.media.deletion import MediaDeletionRepository
from app.media.deletion_worker import MediaDeletionWorker
from app.media.repository import MediaRepository
from app.media.service import MediaService
from app.media.storage import OssMediaStorage
from app.memory.candidate import MemoryCandidateExtractionService
from app.memory.candidate_repository import MemoryCandidateRepository
from app.memory.candidate_service import MemoryCandidateService
from app.memory.candidate_worker import MemoryCandidateExpiryWorker
from app.memory.extraction_worker import MemoryExtractionWorker
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.memory.source import MemorySourceRepository
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.preferences import NotificationPreferenceRepository
from app.notifications.templates import NotificationTemplateRepository
from app.proactive.candidate_repository import ProactiveCandidateRepository
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
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.legacy_bridge import LegacyConversationBridge
from app.streaming.multimodal_worker import MultimodalWorker
from app.streaming.repository import ChatRunRepository
from app.streaming.service import ChatRunManager
from app.voice.service import VoiceError, VoiceMetrics, VoiceService

runtime_settings = get_settings()
configure_logging(runtime_settings.log_level)
logger = structlog.get_logger(__name__)
http_metrics = HttpMetrics.create(
    service_name=runtime_settings.service_name,
    service_version=runtime_settings.service_version,
    environment=runtime_settings.environment,
)
voice_metrics = VoiceMetrics(http_metrics.registry)
mcp_metrics = McpRuntimeMetrics(http_metrics.registry)
multimodal_metrics = MultimodalMetrics(http_metrics.registry)


@asynccontextmanager
async def lifespan(app: FastAPI, *, role: str = "api") -> AsyncIterator[None]:
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
    app.state.metrics = http_metrics
    app.state.voice = VoiceService(settings, metrics=voice_metrics)
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
    app.state.model_usage_repository = ModelUsageRepository(app.state.database)
    app.state.models = ModelGateway(
        settings,
        telemetry=app.state.trulens_telemetry,
        metrics=http_metrics,
        usage_sink=(
            app.state.model_usage_repository.write if settings.llm_usage_ledger_enabled else None
        ),
    )
    if settings.llm_global_concurrency_enabled:
        app.state.models.protection.shared = RedisModelLease(
            app.state.cache.client,
            provider=f"{settings.llm_base_url}/{settings.llm_model}",
            limit=settings.llm_global_max_concurrency,
            ttl=settings.llm_timeout_seconds * settings.llm_max_attempts + 30,
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
        metrics=http_metrics,
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
    # Workers do not connect to MCP or depend on its readiness. Internal checks use Gateway.
    app.state.mcp_tools = (
        McpBusinessToolsClient(settings) if role == "api" and settings.mcp_configured else None
    )
    app.state.business_tools = RoutedBusinessToolsClient(
        settings,
        app.state.gateway,
        app.state.mcp_tools,
    )
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
    app.state.proactive_candidates = ProactiveCandidateRepository()
    app.state.decision_repository = DecisionRepository()
    app.state.decision_service = DecisionPlatformService(
        app.state.database, app.state.decision_repository, metrics=http_metrics
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
        student_context_reader=GatewayStudentTrainingContextReader(
            app.state.gateway, app.state.memory_service
        ),
        max_output_tokens=settings.training_plan_max_output_tokens,
    )
    app.state.tool_registry = build_fitness_tool_registry(
        cast(GatewayClient, app.state.business_tools),
        plan_generator=app.state.training_plan_generator,
        memory_service=app.state.memory_service,
        operations_audit_repository=app.state.operations_audit,
        operations_rate_limit_cache=app.state.cache,
        operations_rate_limit_requests=settings.operations_rate_limit_requests,
        operations_rate_limit_window_seconds=settings.operations_rate_limit_window_seconds,
        operations_query_timeout_seconds=settings.operations_query_timeout_seconds,
        operations_metrics=http_metrics,
        operations_decision_snapshot_sink=lambda organization_id, snapshot, trace_id: (
            app.state.decision_service.ingest_snapshot(
                organization_id=organization_id,
                payload=snapshot,
                trace_id=trace_id,
            )
        ),
        telemetry=app.state.trulens_telemetry,
        availability_checker=app.state.business_tools.is_available if role == "api" else None,
    )
    # 确认参数进入 PostgreSQL 前必须经过应用层加密；密钥缺失时拒绝启动，避免形成
    # “看似持久化、实际明文落库”的不安全降级路径。
    app.state.confirmation_cipher = AesGcmPayloadCipher.from_base64(
        settings.confirmation_encryption_key_base64,
        settings.confirmation_encryption_key_version,
        settings.confirmation_encryption_decryption_key_ring,
    )
    app.state.memory_source_retention = MemorySourceRepository(
        app.state.database,
        app.state.confirmation_cipher,
        retention_days=settings.memory_extraction_source_retention_days,
    )
    app.state.memory_sources = (
        app.state.memory_source_retention if settings.memory_async_extraction_enabled else None
    )
    app.state.chat_runs = ChatRunManager(
        ChatRunRepository(
            app.state.database,
            app.state.confirmation_cipher,
            timeout=settings.chat_run_timeout_seconds,
            retention_hours=settings.chat_run_retention_hours,
            memory_sources=app.state.memory_sources,
        ),
        timeout=settings.chat_run_timeout_seconds,
        heartbeat=settings.chat_stream_heartbeat_seconds,
        queue_size=settings.chat_stream_queue_size,
        shutdown_timeout=settings.worker_shutdown_timeout_seconds,
        metrics=http_metrics,
    )
    app.state.browser_auth = (
        BrowserAuthService(
            settings,
            app.state.cache.client,
            app.state.confirmation_cipher,
            app.state.context_verifier,
        )
        if settings.browser_auth_enabled
        else None
    )
    app.state.mobile_auth_upstream = None
    app.state.mobile_auth = None
    if settings.mobile_auth_enabled:
        app.state.mobile_auth_upstream = app.state.browser_auth or BrowserAuthService(
            settings,
            app.state.cache.client,
            app.state.confirmation_cipher,
            app.state.context_verifier,
        )
        app.state.mobile_auth = MobileAuthService(
            settings,
            app.state.cache.client,
            app.state.confirmation_cipher,
            app.state.context_verifier,
            app.state.mobile_auth_upstream,
        )
    app.state.multimodal_gateway = None
    app.state.multimodal_repository = None
    app.state.multimodal_worker = None
    app.state.media_service = None
    app.state.media_deletion = None
    app.state.media_deletion_worker = None
    if settings.multimodal_api_enabled:
        storage = OssMediaStorage(
            endpoint=settings.multimodal_oss_endpoint,
            bucket_name=settings.multimodal_oss_bucket,
            region=settings.multimodal_oss_region,
            access_key_id=settings.multimodal_oss_access_key_id,
            access_key_secret=settings.multimodal_oss_access_key_secret,
            security_token=settings.multimodal_oss_security_token,
        )
        media_repository = MediaRepository(app.state.database)
        app.state.media_service = MediaService(
            media_repository,
            storage,
            validator_socket=settings.multimodal_validator_socket,
            validator_temp_dir=settings.multimodal_validator_temp_dir,
        )
        app.state.media_deletion = MediaDeletionRepository(
            app.state.database, app.state.confirmation_cipher
        )
        app.state.media_deletion_worker = MediaDeletionWorker(
            app.state.database, storage, metrics=multimodal_metrics
        )
        app.state.multimodal_repository = DurableMultimodalRepository(
            app.state.database, app.state.confirmation_cipher
        )
        app.state.multimodal_gateway = MultimodalGateway(
            settings,
            usage_sink=app.state.model_usage_repository.write
            if settings.llm_usage_ledger_enabled
            else None,
        )
        app.state.multimodal_gateway.protection.shared = RedisModelLease(
            app.state.cache.client,
            provider=f"multimodal:{settings.multimodal_base_url}/{settings.multimodal_model}",
            limit=settings.multimodal_global_max_concurrency,
            ttl=settings.multimodal_timeout_seconds + 60,
        )
        app.state.multimodal_gateway.call_budget = RedisDailyModelBudget(
            app.state.cache.client,
            provider=f"multimodal:{settings.multimodal_base_url}/{settings.multimodal_model}",
            limit=settings.multimodal_daily_max_calls,
        )
        app.state.multimodal_worker = MultimodalWorker(
            app.state.multimodal_repository,
            media_repository,
            storage,
            MovementAnalysisService(
                app.state.multimodal_gateway,
                app.state.rag_service,
                prompt_version=settings.multimodal_prompt_version,
                memory_service=app.state.memory_service,
            ),
            app.state.mobile_auth.identity,
            metrics=multimodal_metrics,
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
    app.state.memory_extraction_worker = MemoryExtractionWorker(
        app.state.database,
        app.state.confirmation_cipher,
        app.state.memory_candidate_service,
        max_attempts=settings.memory_extraction_max_attempts,
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
        route_selector=app.state.business_tools.route_for_tool,
    )
    app.state.confirmation_reconciliation_worker = ConfirmationReconciliationWorker(
        app.state.confirmation_service.repository,
        reconciler=app.state.confirmation_service,
        older_than_seconds=max(60, settings.confirmation_ttl_seconds // 2),
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
    # Memory 撤销/纠正后立即失效同一主体的派生会话摘要，避免旧偏好继续注入模型。
    app.state.memory_service.summary_repository = app.state.session_summary_service.repository
    app.state.memory_service.checkpoint_cleaner = app.state.checkpoint_store
    app.state.session_lock = SessionLockManager(
        app.state.cache.client,
        ttl_seconds=settings.session_lock_ttl_seconds,
    )
    maintenance_task: asyncio.Task[None] | None = None
    app.state.draining = False
    try:
        if app.state.mcp_tools is not None:
            # A dependency outage hides remote capabilities; local capabilities still start.
            await app.state.mcp_tools.start()
        # Checkpointer 在服务启动阶段创建官方表结构；如果数据库不可用则拒绝启动，
        # 避免服务看似在线却丢失会话状态。
        await app.state.checkpoint_store.start()
        app.state.legacy_bridge = LegacyConversationBridge(
            app.state.checkpoint_store.saver, app.state.chat_runs.repository
        )
        if app.state.multimodal_worker is not None:
            app.state.multimodal_worker.legacy_bridge = app.state.legacy_bridge
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
            memory_service=app.state.memory_service,
            session_summary_service=app.state.session_summary_service,
            telemetry=app.state.trulens_telemetry,
            intent_router=HybridIntentRouter(
                app.state.models,
                mode=settings.router_mode,
                min_confidence=settings.router_min_confidence,
                timeout_seconds=settings.router_timeout_seconds,
                max_output_tokens=settings.router_max_output_tokens,
                context_messages=settings.router_context_messages,
                metrics=http_metrics,
            ),
            chat_run_repository=app.state.chat_runs.repository,
        )
        app.state.chat_runs.cancel_confirmations = (
            app.state.confirmation_service.repository.cancel_for_run
        )
        maintenance_task = asyncio.create_task(_runtime_retention(app))
        yield
    finally:
        app.state.draining = True
        await app.state.chat_runs.close()
        if maintenance_task is not None:
            maintenance_task.cancel()
            await asyncio.gather(maintenance_task, return_exceptions=True)
        # 即使请求处理出现异常，FastAPI 仍会进入 finally，确保连接被关闭。
        await app.state.models.close()
        await app.state.voice.close()
        if app.state.multimodal_gateway is not None:
            await app.state.multimodal_gateway.close()
        if app.state.browser_auth is not None:
            await app.state.browser_auth.close()
        if (
            app.state.mobile_auth_upstream is not None
            and app.state.mobile_auth_upstream is not app.state.browser_auth
        ):
            await app.state.mobile_auth_upstream.close()
        if app.state.mcp_tools is not None:
            await app.state.mcp_tools.close()
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
app.add_middleware(BrowserSessionMiddleware)
app.add_exception_handler(BrowserAuthError, auth_error)
app.add_exception_handler(VoiceError, voice_error)
app.add_exception_handler(TimeoutError, auth_error)
from redis.exceptions import RedisError

app.add_exception_handler(RedisError, auth_error)
app.include_router(auth_router)
app.include_router(mobile_auth_router)
app.include_router(mobile_media_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(capabilities_router)
app.include_router(confirmations_router)
app.include_router(memory_candidates_router)
app.include_router(memories_router)
app.include_router(notifications_router)
app.include_router(admin_knowledge_router)
app.include_router(admin_notifications_router)
app.include_router(admin_operations_router)
app.include_router(admin_proactive_router)
app.include_router(admin_decisions_router)
app.include_router(knowledge_review_router)
app.include_router(rag_router)
app.include_router(health_router)


async def _runtime_retention(application: FastAPI) -> None:
    """小批量清理短期结果和用量；多实例 SKIP LOCKED 避免长事务。"""
    while True:
        try:
            await application.state.chat_runs.repository.cleanup()
            await application.state.chat_runs.repository.cleanup_history(
                application.state.checkpoint_store.existing_threads
            )
            if application.state.settings.llm_usage_ledger_enabled:
                await application.state.model_usage_repository.cleanup(
                    application.state.settings.llm_usage_retention_days
                )
            mcp_metrics.reconciliation(
                await application.state.confirmation_service.repository.reconciliation_stats()
            )
        except Exception as exc:  # noqa: BLE001 - 保留期限任务失败不能终止 API
            logger.error("runtime_retention_failed", error_type=type(exc).__name__)
        await asyncio.sleep(60)


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
    mcp_metrics.dependency(
        enabled=runtime_settings.mcp_enabled, client=getattr(app.state, "mcp_tools", None)
    )
    return Response(
        content=generate_latest(http_metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
