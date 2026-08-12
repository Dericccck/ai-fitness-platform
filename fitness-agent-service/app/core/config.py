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

    gateway_context_signing_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GATEWAY_CONTEXT_SIGNING_SECRET", "AGENT_GATEWAY_CONTEXT_SIGNING_SECRET"
        ),
    )
    gateway_context_max_ttl_seconds: int = Field(default=300, ge=60, le=900)

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
    agent_max_tool_steps: int = Field(default=4, ge=1, le=8)
    llm_thinking_enabled: bool = False

    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1536, ge=1, le=4000)

    reranker_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    reranker_timeout_seconds: float = 15.0

    # RAG limits are bounded at the service boundary. A chat request cannot
    # increase database, reranker, or prompt costs by overriding these values.
    rag_candidate_limit: int = Field(default=20, ge=1, le=100)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_embedding_batch_size: int = Field(default=32, ge=1, le=128)
    rag_chunk_max_chars: int = Field(default=1200, ge=400, le=4000)
    rag_chunk_overlap_chars: int = Field(default=120, ge=0, le=500)
    rag_max_source_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    rag_staging_dir: str = "./var/rag-staging"
    rag_storage_backend: Literal["local", "s3"] = "local"
    rag_s3_endpoint_url: str = ""
    rag_s3_region: str = "us-east-1"
    rag_s3_bucket: str = "fitness-agent-knowledge"
    rag_s3_access_key: str = ""
    rag_s3_secret_key: str = ""
    rag_ingestion_max_attempts: int = Field(default=3, ge=1, le=5)
    rag_ingestion_worker_batch_size: int = Field(default=10, ge=1, le=100)

    gateway_base_url: str = "http://127.0.0.1:8081"
    gateway_internal_service_token: str = ""
    gateway_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
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

        return bool(self.embedding_effective_api_key and self.embedding_model)

    @property
    def llm_configured(self) -> bool:
        """判断 LLM 是否具备真实密钥和明确模型，禁止隐式使用默认模型。"""

        return bool(self.llm_api_key and self.llm_model)

    @property
    def reranker_configured(self) -> bool:
        """判断 Reranker 端点和模型是否已配置。部分内网端点可不需要 API Key。"""

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
