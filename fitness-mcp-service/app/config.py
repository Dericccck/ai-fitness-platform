"""Fail-closed adapter settings; all credentials are supplied outside source control."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resource: str = Field(min_length=1, max_length=2048)
    scopes: frozenset[Literal["fitness:read", "fitness:write"]]
    expires_at: int = Field(gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_", extra="ignore")
    environment: Literal["development", "test", "staging", "production"] = "development"
    service_credentials_json: str = "[]"
    resource_url: str = "http://localhost:8001/mcp"
    gateway_base_url: str = ""
    gateway_service_token: str = ""
    allowed_hosts: list[str] = ["localhost:*", "localhost", "127.0.0.1:*", "127.0.0.1"]
    allowed_origins: list[str] = ["http://localhost:*", "http://127.0.0.1:*"]
    context_algorithm: Literal["RS256", "HS256"] = "RS256"
    context_key_id: str = "legacy"
    context_public_keys_json: str = "{}"
    context_jwks_url: str = ""
    context_jwks_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    context_secret: str = ""
    context_secret_ring_json: str = "{}"
    allow_insecure_hs256: bool = False
    context_max_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    max_deadline_seconds: float = Field(default=10.0, gt=0, le=120, allow_inf_nan=False)
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=10, allow_inf_nan=False)
    max_request_bytes: int = Field(default=262144, ge=1024, le=4194304)
    max_result_bytes: int = Field(default=1048576, ge=1024, le=4194304)
    max_concurrent_gateway_calls: int = Field(default=100, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_trust(self) -> Settings:
        from urllib.parse import urlsplit

        for value in (self.gateway_base_url, self.resource_url, self.context_jwks_url):
            if not value:
                continue
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.fragment
                or url.query
            ):
                raise ValueError(
                    "Service URLs must be fixed HTTP(S) URLs without credentials/query/fragment"
                )
        if self.context_algorithm == "HS256":
            if not self.allow_insecure_hs256 or self.environment not in {"development", "test"}:
                raise ValueError("HS256 is allowed only with an explicit local/test opt-in")
            if len(self.context_secret) < 32:
                raise ValueError("Local HS256 context secret must have at least 32 characters")
        if not self.allowed_hosts:
            raise ValueError("An explicit MCP Host allowlist is required")
        for host in self.allowed_hosts:
            hostname = host.removesuffix(":*")
            if not hostname or "*" in hostname or "/" in hostname:
                raise ValueError(
                    "Host allowlist requires exact hostnames; only a port :* is allowed"
                )
        if (
            self.environment == "production"
            and self.context_jwks_url
            and urlsplit(self.context_jwks_url).scheme != "https"
        ):
            raise ValueError("Production JWKS URL must use HTTPS")
        return self
