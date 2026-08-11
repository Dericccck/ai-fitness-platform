from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "local"
    host: str = "0.0.0.0"
    port: int = 8090

    database_url: str = "postgresql+asyncpg://fitness_agent:fitness_agent@127.0.0.1:5433/fitness_agent"
    redis_url: str = "redis://127.0.0.1:6380/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""

    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""

    reranker_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    reranker_timeout_seconds: float = 15.0

    internal_service_token: str = ""

    @computed_field
    @property
    def embedding_effective_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @computed_field
    @property
    def embedding_configured(self) -> bool:
        return bool(self.embedding_effective_api_key and self.embedding_model)

    @computed_field
    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @computed_field
    @property
    def reranker_configured(self) -> bool:
        return bool(self.reranker_url and self.reranker_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
