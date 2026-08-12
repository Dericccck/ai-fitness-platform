"""OCR 服务的环境变量配置。"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OCR 服务与推理引擎共享的运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OCR_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    host: str = "0.0.0.0"
    port: int = Field(default=8091, ge=1, le=65535)
    log_level: str = "INFO"
    api_docs_enabled: bool = False
    api_key: str = ""
    auth_required: bool = True
    max_source_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_pages: int = Field(default=50, ge=1, le=500)
    max_concurrency: int = Field(default=1, ge=1, le=16)
    inference_timeout_seconds: float = Field(default=300.0, gt=0, le=1800)
    device: str = "cpu"
    language: str = "ch"
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = True
    use_table_recognition: bool = True
    use_formula_recognition: bool = False
    format_block_content: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级配置快照。"""

    return Settings()
