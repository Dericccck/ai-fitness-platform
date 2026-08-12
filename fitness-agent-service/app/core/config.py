from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent 服务唯一的运行时配置入口。

    所有配置都使用 ``AGENT_`` 前缀从环境变量或本地 ``.env`` 读取。生产环境只允许
    通过部署平台的 Secret Manager 注入敏感值，不能把 API Key 或数据库密码写进代码。
    Agent、API 和基础设施适配器接收同一个 Settings 实例，保证不同模块不会各自采用
    不一致的默认值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        case_sensitive=False,
        extra="ignore",
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
    redis_url: str = "redis://127.0.0.1:6380/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=1200, ge=128, le=8192)
    agent_max_tool_steps: int = Field(default=4, ge=1, le=8)

    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""

    reranker_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    reranker_timeout_seconds: float = 15.0

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


@lru_cache
def get_settings() -> Settings:
    """在进程内只解析一次配置，避免请求之间出现环境读取差异。"""

    return Settings()
