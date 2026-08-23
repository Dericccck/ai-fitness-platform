from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
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
    log_level: str = "INFO"
    api_docs_enabled: bool = True

    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_traces_endpoint: str = ""
    otel_export_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    otel_trace_sample_ratio: float = Field(default=0.1, ge=0, le=1)

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
    # 页面路由阈值由部署配置统一控制，上传者和 LLM 无权覆盖。默认值偏保守，
    # 用于把可能承载健身动作、姿态或风险信息的图片密集页送入专业审核。
    rag_pdf_min_image_area_ratio: float = Field(default=0.45, ge=0, le=1)
    rag_pdf_max_image_page_text_chars: int = Field(default=600, ge=0, le=20_000)
    rag_pdf_max_image_page_text_area_ratio: float = Field(default=0.25, ge=0, le=1)
    rag_pdf_min_ocr_text_chars: int = Field(default=12, ge=0, le=1_000)
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
