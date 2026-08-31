from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent 服务唯一的运行时配置入口。

    除了与学习项目保持一致的 ``DEEPSEEK_*`` 模型变量外，其余配置使用 ``AGENT_``
    前缀从环境变量或本地 ``.env`` 读取。生产环境只允许通过部署平台的 Secret Manager
    注入敏感值，不能把 API Key 或数据库密码写进代码。
    Agent、API 和基础设施适配器接收同一个 Settings 实例，保证不同模块不会各自采用
    不一致的默认值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # 部署环境：local 本地开发；test 自动化测试；staging 预发布；production 生产。
    environment: Literal["local", "test", "staging", "production"] = "local"
    host: str = "0.0.0.0"
    port: int = 8090
    service_name: str = "fitness-agent-service"
    service_version: str = "0.1.0"
    # 代码、Prompt、知识库和图编排版本必须随 Trace 一起记录，便于回溯一次回答实际
    # 使用了哪一版实现。service_version 通常绑定 Git commit；source_commit 可在镜像
    # 构建阶段单独注入，避免把发布标签和源码提交混为一谈。
    source_commit: str = Field(
        default="",
        validation_alias=AliasChoices("SOURCE_COMMIT", "AGENT_SOURCE_COMMIT"),
    )
    prompt_version: str = Field(
        default="prompt-v1",
        validation_alias=AliasChoices("PROMPT_VERSION", "AGENT_PROMPT_VERSION"),
    )
    knowledge_base_version: str = Field(
        default="knowledge-v1",
        validation_alias=AliasChoices("KNOWLEDGE_BASE_VERSION", "AGENT_KNOWLEDGE_BASE_VERSION"),
    )
    graph_version: str = Field(
        default="supervisor-domain-subgraphs-v1",
        validation_alias=AliasChoices("GRAPH_VERSION", "AGENT_GRAPH_VERSION"),
    )
    log_level: str = "INFO"
    api_docs_enabled: bool = True

    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_traces_endpoint: str = ""
    otel_export_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    otel_trace_sample_ratio: float = Field(default=0.1, ge=0, le=1)

    # TruLens 使用现有 OTEL provider 记录有界语义 span。评测采集默认关闭，因为其中可能
    # 包含已脱敏的用户文本；生产环境通常应使用元数据模式，并在受限环境中运行 Judge 评测。
    trulens_enabled: bool = False
    trulens_capture_mode: Literal["disabled", "metadata", "evaluation"] = "disabled"
    trulens_capture_max_chars: int = Field(default=2000, ge=256, le=8000)
    # 在线 OTEL 导出会把 TruLens 语义 Span 直接写入独立评测库。默认关闭，只有明确
    # 配置独立数据库并打开 metadata/evaluation 采集时才启用，避免误写业务库。
    trulens_online_export_enabled: bool = False
    trulens_retention_days: int = Field(default=30, ge=1, le=3650)
    # SHA-256 对低熵用户 ID 可能被枚举；提供密钥后使用 HMAC-SHA256 生成关联摘要。
    # 本地为空时保留兼容行为，生产环境由 Secret Manager 注入并在契约检查中强制要求。
    trulens_identifier_hash_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRULENS_IDENTIFIER_HASH_SECRET", "AGENT_TRULENS_IDENTIFIER_HASH_SECRET"
        ),
    )
    trulens_database_url: str = Field(
        default="sqlite:///./var/trulens/trulens.sqlite",
        validation_alias=AliasChoices("TRULENS_DATABASE_URL", "AGENT_TRULENS_DATABASE_URL"),
    )
    trulens_judge_enabled: bool = False
    trulens_judge_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("TRULENS_JUDGE_BASE_URL", "AGENT_TRULENS_JUDGE_BASE_URL"),
    )
    trulens_judge_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TRULENS_JUDGE_API_KEY", "AGENT_TRULENS_JUDGE_API_KEY"),
    )
    trulens_judge_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("TRULENS_JUDGE_MODEL", "AGENT_TRULENS_JUDGE_MODEL"),
    )
    trulens_judge_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    database_url: str = (
        "postgresql+asyncpg://fitness_agent:fitness_agent@127.0.0.1:5433/fitness_agent"
    )
    checkpoint_database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6380/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    checkpoint_pool_min_size: int = Field(default=1, ge=1, le=10)
    checkpoint_pool_max_size: int = Field(default=5, ge=1, le=30)
    session_lock_ttl_seconds: int = Field(default=60, ge=10, le=300)
    # 确认单精确参数使用 AES-GCM 加密；密钥只允许通过 Secret 注入，不能写入仓库。
    confirmation_encryption_key_base64: str = ""
    confirmation_encryption_key_version: str = "local-v1"
    confirmation_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    # 与 Java Gateway v1 兼容的服务端签发密钥；生产环境必须与 Gateway 使用 Secret Manager
    # 管理的同一密钥，不能通过浏览器或模型传入。
    confirmation_signing_secret: str = ""
    confirmation_token_ttl_seconds: int = Field(default=120, ge=30, le=600)
    confirmation_signing_algorithm: str = Field(
        default="HS256",
        validation_alias=AliasChoices(
            "CONFIRMATION_SIGNING_ALGORITHM", "AGENT_CONFIRMATION_SIGNING_ALGORITHM"
        ),
    )
    confirmation_signing_key_id: str = Field(
        default="legacy",
        validation_alias=AliasChoices(
            "CONFIRMATION_SIGNING_KEY_ID", "AGENT_CONFIRMATION_SIGNING_KEY_ID"
        ),
    )
    confirmation_signing_private_key_pem: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CONFIRMATION_SIGNING_PRIVATE_KEY_PEM",
            "AGENT_CONFIRMATION_SIGNING_PRIVATE_KEY_PEM",
        ),
    )

    gateway_context_signing_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_SIGNING_SECRET", "AGENT_GATEWAY_CONTEXT_SIGNING_SECRET"
        ),
    )
    gateway_context_max_ttl_seconds: int = Field(default=300, ge=60, le=900)
    gateway_context_signing_algorithm: str = Field(
        default="HS256",
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_SIGNING_ALGORITHM", "AGENT_GATEWAY_CONTEXT_SIGNING_ALGORITHM"
        ),
    )
    gateway_context_signing_key_id: str = Field(
        default="legacy",
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_SIGNING_KEY_ID", "AGENT_GATEWAY_CONTEXT_SIGNING_KEY_ID"
        ),
    )
    gateway_context_signing_key_ring: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_SIGNING_KEY_RING", "AGENT_GATEWAY_CONTEXT_SIGNING_KEY_RING"
        ),
    )
    gateway_context_verification_public_key_ring: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_VERIFICATION_PUBLIC_KEY_RING",
            "AGENT_GATEWAY_CONTEXT_VERIFICATION_PUBLIC_KEY_RING",
        ),
    )
    gateway_context_verification_jwks_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_VERIFICATION_JWKS_URL",
            "AGENT_GATEWAY_CONTEXT_VERIFICATION_JWKS_URL",
        ),
    )
    gateway_context_verification_jwks_cache_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_VERIFICATION_JWKS_CACHE_SECONDS",
            "AGENT_GATEWAY_CONTEXT_VERIFICATION_JWKS_CACHE_SECONDS",
        ),
    )
    gateway_context_verification_jwks_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=10,
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_VERIFICATION_JWKS_TIMEOUT_SECONDS",
            "AGENT_GATEWAY_CONTEXT_VERIFICATION_JWKS_TIMEOUT_SECONDS",
        ),
    )

    # 优先读取学习项目使用的 DEEPSEEK_* 变量；AGENT_LLM_* 仅作为历史兼容配置，
    # 避免已有本地环境升级时突然失效。生产部署建议统一使用 DEEPSEEK_*。
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices(
            "DEEPSEEK_BASE_URL", "AGENT_DEEPSEEK_BASE_URL", "AGENT_LLM_BASE_URL"
        ),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEEPSEEK_API_KEY", "AGENT_DEEPSEEK_API_KEY", "AGENT_LLM_API_KEY"
        ),
    )
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEEPSEEK_MODEL", "AGENT_DEEPSEEK_MODEL", "AGENT_LLM_MODEL"),
    )
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=1200, ge=128, le=8192)
    # 结构化训练计划包含多天和动作明细，单独使用更大的输出预算，避免 JSON
    # 在动作 notes 或最后一个训练日中途被模型截断；普通对话和经营查询仍使用
    # llm_max_output_tokens，防止所有请求无差别增加 Token 成本。
    training_plan_max_output_tokens: int = Field(default=3000, ge=512, le=8192)
    agent_max_tool_steps: int = Field(default=4, ge=1, le=8)
    llm_thinking_enabled: bool = False

    # Embedding 后端：openai 走远程 API；local 加载本地模型文件。切换后必须重建向量索引。
    embedding_backend: Literal["openai", "local"] = "openai"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_model_path: str = ""
    embedding_dimensions: int = Field(default=1024, ge=1, le=4000)

    # Reranker 后端：http 调用独立服务；local 在 Agent 进程内加载模型。
    reranker_backend: Literal["http", "local"] = "http"
    reranker_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    reranker_model_path: str = ""
    reranker_timeout_seconds: float = 15.0

    # RAG 限制在服务边界统一约束。对话请求不能通过覆盖这些值增加数据库、
    # Reranker 或 Prompt 成本。
    rag_candidate_limit: int = Field(default=20, ge=1, le=100)
    rag_keyword_candidate_limit: int = Field(default=20, ge=1, le=100)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_embedding_batch_size: int = Field(default=32, ge=1, le=128)
    rag_chunk_max_chars: int = Field(default=1200, ge=400, le=4000)
    rag_chunk_overlap_chars: int = Field(default=120, ge=0, le=500)
    rag_vector_weight: float = Field(default=0.6, ge=0, le=1)
    rag_keyword_weight: float = Field(default=0.4, ge=0, le=1)
    rag_rrf_k: int = Field(default=60, ge=1, le=200)
    rag_max_source_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    rag_staging_dir: str = "./var/rag-staging"
    # 知识原文件存储：local 仅适合本地；s3 对接对象存储并承担生命周期和访问控制。
    rag_storage_backend: Literal["local", "s3"] = "local"
    rag_s3_endpoint_url: str = ""
    rag_s3_region: str = "us-east-1"
    rag_s3_bucket: str = "fitness-agent-knowledge"
    rag_s3_access_key: str = ""
    rag_s3_secret_key: str = ""
    rag_ingestion_max_attempts: int = Field(default=3, ge=1, le=5)
    rag_ingestion_worker_batch_size: int = Field(default=10, ge=1, le=100)
    rag_reindex_worker_batch_size: int = Field(default=2, ge=1, le=20)
    rag_reindex_worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    # Memory 候选过期由独立 Worker 执行，避免 API 进程重启或多实例部署造成清理任务丢失。
    memory_candidate_expiry_batch_size: int = Field(default=500, ge=1, le=5000)
    memory_candidate_expiry_poll_seconds: float = Field(default=60.0, ge=1, le=3600)
    memory_candidate_worker_metrics_port: int = Field(default=8092, ge=1, le=65535)
    # 正式 Memory 与候选进入终态后仍保留有限期限用于用户追踪和故障排查，期限到达后由
    # 独立 Worker 清除正文；不可变生命周期审计事件不随正文删除。
    memory_terminal_retention_days: int = Field(default=90, ge=1, le=3650)
    memory_candidate_terminal_retention_days: int = Field(default=30, ge=1, le=3650)
    memory_retention_batch_size: int = Field(default=500, ge=1, le=5000)
    memory_retention_poll_seconds: float = Field(default=3600.0, ge=1, le=86400)
    memory_retention_worker_metrics_port: int = Field(default=8094, ge=1, le=65535)
    # 短期会话摘要只压缩当前 thread 的上下文，不是长期 Memory；达到阈值后用 DeepSeek
    # 生成加密摘要，并把 Checkpoint 历史压缩为摘要加最近几轮消息。
    session_summary_trigger_messages: int = Field(default=12, ge=2, le=100)
    session_summary_keep_recent_messages: int = Field(default=6, ge=1, le=50)
    session_summary_max_chars: int = Field(default=3000, ge=500, le=4000)
    session_summary_max_input_chars: int = Field(default=12000, ge=1000, le=50000)
    session_summary_retention_days: int = Field(default=7, ge=1, le=365)
    session_summary_batch_size: int = Field(default=500, ge=1, le=5000)
    session_summary_poll_seconds: float = Field(default=3600.0, ge=1, le=86400)
    session_summary_worker_metrics_port: int = Field(default=8095, ge=1, le=65535)
    # 站内通知由独立 Outbox Worker 发布，避免 API 进程重启造成通知任务滞留。
    notification_worker_batch_size: int = Field(default=100, ge=1, le=500)
    notification_worker_poll_seconds: float = Field(default=5.0, ge=1, le=3600)
    notification_worker_metrics_port: int = Field(default=8093, ge=1, le=65535)
    # 未显式配置通知偏好时使用的本地时区；用户保存偏好后以用户自己的 IANA 时区为准。
    notification_default_timezone: str = "Asia/Shanghai"
    # Proactive Agent 只在独立 Worker 中启用；API 进程不主动连接 RabbitMQ，避免把消息消费
    # 生命周期和 HTTP 请求生命周期耦合。RabbitMQ 事件进入 PostgreSQL Inbox 后再转为通知 Outbox。
    proactive_worker_enabled: bool = False
    proactive_rabbitmq_url: str = "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/"
    proactive_rabbitmq_exchange: str = "fitness.domain.events"
    proactive_rabbitmq_queue: str = "fitness.proactive.events"
    proactive_rabbitmq_routing_key: str = (
        "appointment.created,appointment.rescheduled,appointment.cancelled,"
        "training.plan.review_required,training.plan.published"
    )
    proactive_worker_batch_size: int = Field(default=50, ge=1, le=500)
    proactive_worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    proactive_worker_metrics_port: int = Field(default=8096, ge=1, le=65535)
    # RabbitMQ 网络抖动或 Broker 重启时由消费器使用有上限的指数退避重连，避免
    # 连接失败形成忙循环；生产环境可按 Broker 和告警恢复窗口调整，但不能关闭重连。
    proactive_rabbitmq_reconnect_initial_seconds: float = Field(default=1.0, gt=0, le=30)
    proactive_rabbitmq_reconnect_max_seconds: float = Field(default=30.0, gt=0, le=300)
    # 页面路由阈值由部署配置统一控制，上传者和 LLM 无权覆盖。默认值偏保守，
    # 用于把可能承载健身动作、姿态或风险信息的图片密集页送入专业审核。
    rag_pdf_min_image_area_ratio: float = Field(default=0.45, ge=0, le=1)
    rag_pdf_max_image_page_text_chars: int = Field(default=600, ge=0, le=20_000)
    rag_pdf_max_image_page_text_area_ratio: float = Field(default=0.25, ge=0, le=1)
    rag_pdf_min_ocr_text_chars: int = Field(default=12, ge=0, le=1_000)
    rag_pdf_min_ocr_confidence: float = Field(default=0.75, ge=0, le=1)
    # 离线评测和线上上传审核必须使用同一类指标。生产值应由真实健身资料回归后
    # 固化到部署配置，不能让 API 请求或 LLM 临时放宽门禁。
    rag_quality_max_noise_rate: float = Field(default=0.0, ge=0, le=1)
    rag_quality_max_fragment_rate: float = Field(default=0.35, ge=0, le=1)
    rag_quality_max_duplicate_rate: float = Field(default=0.02, ge=0, le=1)
    rag_quality_min_parent_integrity: float = Field(default=1.0, ge=0, le=1)
    rag_quality_min_table_integrity: float = Field(default=1.0, ge=0, le=1)
    rag_quality_max_missing_pages: int = Field(default=0, ge=0, le=10_000)
    rag_quality_max_ocr_required_pages: int = Field(default=0, ge=0, le=10_000)
    # OCR 后端：disabled 表示扫描型/低文字页会被门禁拦截；http 调用独立 OCR 服务。
    rag_ocr_backend: Literal["disabled", "http"] = "disabled"
    rag_ocr_endpoint_url: str = ""
    rag_ocr_api_key: str = ""
    rag_ocr_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    rag_ocr_max_response_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    # 杀毒后端：structural 只有格式/压缩包结构检查，不代表无病毒；clamav 才提供外部病毒 verdict。
    rag_malware_scanner_backend: Literal["structural", "clamav"] = "structural"
    rag_clamav_host: str = "127.0.0.1"
    rag_clamav_port: int = Field(default=3310, ge=1, le=65535)
    rag_clamav_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    gateway_base_url: str = "http://127.0.0.1:8081"
    gateway_internal_service_token: str = ""
    gateway_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    operations_query_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    operations_rate_limit_requests: int = Field(default=60, ge=1, le=10_000)
    operations_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    gateway_max_retries: int = Field(default=2, ge=0, le=5)
    gateway_retry_backoff_seconds: float = Field(default=0.1, ge=0, le=5)

    @model_validator(mode="after")
    def validate_recovery_configuration(self) -> "Settings":
        """在服务启动阶段校验 RabbitMQ 重连退避范围。

        最大退避时间小于初始时间会让配置语义失真，并可能在故障时形成异常的
        重连节奏。提前拒绝这类配置，避免 Worker 启动后才暴露问题。
        """

        if self.proactive_rabbitmq_reconnect_max_seconds < (
            self.proactive_rabbitmq_reconnect_initial_seconds
        ):
            raise ValueError("主动事件 RabbitMQ 最大重连延迟不能小于初始延迟")
        return self

    @model_validator(mode="after")
    def validate_trulens_online_contract(self) -> "Settings":
        """防止开启在线评测后却没有实际 Trace 采集链路。"""

        if not self.trulens_online_export_enabled or self.environment == "production":
            return self
        errors: list[str] = []
        if not self.trulens_enabled:
            errors.append("启用 TruLens 在线导出时 AGENT_TRULENS_ENABLED 必须为 true")
        if self.trulens_capture_mode == "disabled":
            errors.append("启用 TruLens 在线导出时 AGENT_TRULENS_CAPTURE_MODE 不能为 disabled")
        if not self.otel_configured:
            errors.append(
                "启用 TruLens 在线导出时必须同时提供 AGENT_OTEL_ENABLED 和 OTLP Trace Endpoint"
            )
        if not self.trulens_database_url.strip():
            errors.append("启用 TruLens 在线导出时必须提供 TRULENS_DATABASE_URL")
        if errors:
            raise ValueError("TruLens 在线观测契约不完整：" + "；".join(errors))
        return self

    @model_validator(mode="after")
    def validate_production_authentication_contract(self) -> "Settings":
        """阻止生产环境以本地 HMAC 或空公钥配置启动。

        本地和测试环境允许使用 HMAC，便于离线开发；生产环境必须使用 RS256，
        AgentContext 的验签公钥必须来自认证服务 JWKS 或受控公钥环，确认凭证
        则必须由 Secret Manager 注入 RSA 私钥。这样配置错误会在进程启动时暴露，
        而不是等第一条真实用户请求才失败。
        """

        if self.environment != "production":
            return self
        errors: list[str] = []
        if self.gateway_context_signing_algorithm != "RS256":
            errors.append("GATEWAY_CONTEXT_SIGNING_ALGORITHM 必须为 RS256")
        if not (
            self.gateway_context_verification_jwks_url.strip()
            or self.gateway_context_verification_public_key_ring
        ):
            errors.append("必须提供 GATEWAY_CONTEXT_VERIFICATION_JWKS_URL 或公钥环")
        if self.gateway_context_verification_jwks_url.strip():
            parsed_jwks_url = urlparse(self.gateway_context_verification_jwks_url)
            if parsed_jwks_url.scheme != "https" or not parsed_jwks_url.netloc:
                errors.append("GATEWAY_CONTEXT_VERIFICATION_JWKS_URL 必须使用 HTTPS")
        if not self.gateway_context_signing_key_id.strip():
            errors.append("必须提供 GATEWAY_CONTEXT_SIGNING_KEY_ID")
        if self.confirmation_signing_algorithm != "RS256":
            errors.append("AGENT_CONFIRMATION_SIGNING_ALGORITHM 必须为 RS256")
        if not self.confirmation_signing_key_id.strip():
            errors.append("必须提供 AGENT_CONFIRMATION_SIGNING_KEY_ID")
        if not self.confirmation_signing_private_key_pem.strip():
            errors.append("必须提供 AGENT_CONFIRMATION_SIGNING_PRIVATE_KEY_PEM")
        if errors:
            raise ValueError("生产身份验证契约不完整：" + "；".join(errors))
        return self

    @model_validator(mode="after")
    def validate_production_runtime_contract(self) -> "Settings":
        """阻止生产环境沿用本地开发依赖或不安全的默认值。

        生产配置错误通常不会在进程启动时立刻暴露：例如数据库仍指向 localhost、知识原文件
        仍写入本地磁盘，或者 OCR/ClamAV/Trace 没有接通。这样的实例可能能通过存活探针，
        但会在真实上传、检索或故障排查时才失败。因此这里只校验“必须具备”的部署契约，
        不打印密钥内容，也不要求每个 Worker 都在同一份环境变量里启用。
        """

        if self.environment != "production":
            return self

        errors: list[str] = []
        if self.api_docs_enabled:
            errors.append("AGENT_API_DOCS_ENABLED 必须为 false")
        if not self.metrics_enabled:
            errors.append("AGENT_METRICS_ENABLED 必须为 true")
        if not self.otel_configured:
            errors.append("必须提供 AGENT_OTEL_ENABLED 和 AGENT_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        if self.trulens_capture_mode == "evaluation":
            errors.append("生产环境不允许 AGENT_TRULENS_CAPTURE_MODE=evaluation")
        if self.trulens_online_export_enabled and self.trulens_capture_mode == "disabled":
            errors.append("启用 TruLens 在线导出时 AGENT_TRULENS_CAPTURE_MODE 不能为 disabled")
        if self.trulens_online_export_enabled and not self.trulens_database_url.strip():
            errors.append("启用 TruLens 在线导出时必须提供 TRULENS_DATABASE_URL")
        if self.trulens_online_export_enabled and not self.trulens_identifier_hash_secret.strip():
            errors.append("生产环境启用 TruLens 在线导出时必须提供 TRULENS_IDENTIFIER_HASH_SECRET")
        if self.trulens_online_export_enabled and self.trulens_database_url.strip() in {
            self.database_url.strip(),
            self.checkpoint_database_url.strip(),
        }:
            errors.append("TRULENS_DATABASE_URL 必须使用独立评测库，不能复用业务库或 Checkpoint 库")
        if not self.llm_configured:
            errors.append("必须提供 DEEPSEEK_API_KEY 和 DEEPSEEK_MODEL")
        if not self.gateway_configured:
            errors.append("必须提供 AGENT_GATEWAY_BASE_URL 和 AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN")
        if not self.confirmation_encryption_key_base64.strip():
            errors.append("必须提供 AGENT_CONFIRMATION_ENCRYPTION_KEY_BASE64")
        if self.rag_storage_backend != "s3":
            errors.append("AGENT_RAG_STORAGE_BACKEND 必须为 s3")
        if not (
            self.rag_s3_endpoint_url.strip()
            and self.rag_s3_bucket.strip()
            and self.rag_s3_access_key.strip()
            and self.rag_s3_secret_key.strip()
        ):
            errors.append("必须提供 S3 endpoint、bucket 和凭证")
        if self.rag_malware_scanner_backend != "clamav":
            errors.append("AGENT_RAG_MALWARE_SCANNER_BACKEND 必须为 clamav")
        if self.rag_ocr_backend != "http" or not self.rag_ocr_endpoint_url.strip():
            errors.append("必须提供 AGENT_RAG_OCR_BACKEND=http 和 AGENT_RAG_OCR_ENDPOINT_URL")
        if self._uses_local_host(self.database_url):
            errors.append("生产环境的 AGENT_DATABASE_URL 不能使用 localhost")
        if self._uses_local_host(self.redis_url):
            errors.append("生产环境的 AGENT_REDIS_URL 不能使用 localhost")
        if self._uses_local_host(self.gateway_base_url):
            errors.append("生产环境的 AGENT_GATEWAY_BASE_URL 不能使用 localhost")
        if errors:
            raise ValueError("生产运行时契约不完整：" + "；".join(errors))
        return self

    @staticmethod
    def _uses_local_host(value: str) -> bool:
        """判断连接地址是否仍指向本机，避免生产实例误连自身或宿主机默认端口。"""

        parsed = urlparse(value.strip())
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    @property
    def embedding_effective_api_key(self) -> str:
        """返回 Embedding 实际使用的密钥。

        当 LLM 与 Embedding 使用同一 OpenAI-compatible 服务时允许复用 LLM 密钥；
        如果是不同供应商，则通过独立的 ``AGENT_EMBEDDING_API_KEY`` 覆盖。
        """

        return self.embedding_api_key or self.llm_api_key

    @property
    def embedding_configured(self) -> bool:
        """判断 Embedding 是否具备发起真实请求所需的最小配置。"""

        if self.embedding_backend == "local":
            return bool(self.embedding_model_path)
        return bool(self.embedding_effective_api_key and self.embedding_model)

    @property
    def llm_configured(self) -> bool:
        """判断 LLM 是否具备真实密钥和明确模型，禁止隐式使用默认模型。"""

        return bool(self.llm_api_key and self.llm_model)

    @property
    def reranker_configured(self) -> bool:
        """判断 Reranker 端点和模型是否已配置。部分内网端点可不需要 API Key。"""

        if self.reranker_backend == "local":
            return bool(self.reranker_model_path)
        return bool(self.reranker_url and self.reranker_model)

    @property
    def otel_configured(self) -> bool:
        """只有显式启用并提供端点时才向外部可观测平台发送 Trace。"""

        return bool(self.otel_enabled and self.otel_exporter_otlp_traces_endpoint)

    @property
    def gateway_configured(self) -> bool:
        """判断 Agent 是否具备调用 Java 健身核心 Gateway 的最小配置。"""

        return bool(self.gateway_base_url and self.gateway_internal_service_token)

    @property
    def checkpoint_conninfo(self) -> str:
        """返回 psycopg 使用的连接串，避免把 SQLAlchemy 方言传给 Checkpointer。"""

        connection_url = self.checkpoint_database_url or self.database_url
        return connection_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    """在进程内只解析一次配置，避免请求之间出现环境读取差异。"""

    return Settings()
