import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_provider_configuration_is_explicit() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="llm-key",
        llm_model="fitness-chat-model",
        embedding_model="fitness-embedding-model",
        reranker_url="https://reranker.example.com/v1/rerank",
        reranker_model="fitness-reranker-model",
    )

    assert settings.llm_configured is True
    assert settings.embedding_configured is True
    assert settings.reranker_configured is True
    assert settings.llm_timeout_seconds == 30
    assert settings.agent_max_tool_steps == 4
    assert settings.training_plan_max_output_tokens == 3000
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "fitness-chat-model"
    assert settings.otel_configured is False
    assert settings.gateway_configured is False
    assert settings.operations_query_timeout_seconds == 5
    assert settings.operations_rate_limit_requests == 60
    assert settings.operations_rate_limit_window_seconds == 60
    assert settings.rag_candidate_limit == 20
    assert settings.rag_top_k == 5
    assert settings.rag_chunk_max_chars == 1200
    assert settings.rag_chunk_overlap_chars == 120
    assert settings.rag_quality_max_fragment_rate == 0.35
    assert settings.rag_quality_min_parent_integrity == 1.0
    assert settings.rag_quality_max_missing_pages == 0


def test_deepseek_environment_names_are_supported(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key == "deepseek-key"
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_base_url == "https://api.deepseek.com"


def test_local_embedding_and_reranker_configuration_is_explicit() -> None:
    settings = Settings(
        _env_file=None,
        embedding_backend="local",
        embedding_model_path="/models/bge-m3",
        embedding_dimensions=1024,
        reranker_backend="local",
        reranker_model_path="/models/bge-reranker-base",
    )

    assert settings.embedding_configured is True
    assert settings.embedding_dimensions == 1024
    assert settings.reranker_configured is True


def test_rabbitmq_reconnect_delays_are_validated_at_settings_load() -> None:
    """最大退避时间小于初始时间时，配置加载阶段必须直接失败。"""

    with pytest.raises(ValidationError, match="最大重连延迟不能小于初始延迟"):
        Settings(
            _env_file=None,
            proactive_rabbitmq_reconnect_initial_seconds=10,
            proactive_rabbitmq_reconnect_max_seconds=5,
        )


def test_production_requires_asymmetric_authentication_contract() -> None:
    with pytest.raises(ValidationError, match="生产身份验证契约不完整"):
        Settings(_env_file=None, environment="production")


def test_production_authentication_contract_accepts_rs256_and_jwks() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        api_docs_enabled=False,
        metrics_enabled=True,
        otel_enabled=True,
        otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
        llm_api_key="llm-key",
        llm_model="fitness-chat-model",
        gateway_base_url="http://fitness-core-gateway:8081",
        gateway_internal_service_token="gateway-token",
        confirmation_encryption_key_base64="base64-key",
        rag_storage_backend="s3",
        rag_s3_endpoint_url="https://object-storage.example.com",
        rag_s3_bucket="fitness-agent-knowledge",
        rag_s3_access_key="access-key",
        rag_s3_secret_key="secret-key",
        rag_malware_scanner_backend="clamav",
        rag_ocr_backend="http",
        rag_ocr_endpoint_url="http://ocr-service:8091/v1/parse",
        database_url="postgresql+asyncpg://agent:secret@postgres:5432/fitness_agent",
        redis_url="redis://redis:6379/0",
        gateway_context_signing_algorithm="RS256",
        gateway_context_verification_jwks_url="https://issuer.example/.well-known/jwks.json",
        confirmation_signing_algorithm="RS256",
        confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
    )

    assert settings.environment == "production"


def test_production_rejects_non_https_jwks_url() -> None:
    with pytest.raises(ValidationError, match="必须使用 HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            api_docs_enabled=False,
            metrics_enabled=True,
            otel_enabled=True,
            otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
            llm_api_key="llm-key",
            llm_model="fitness-chat-model",
            gateway_base_url="http://fitness-core-gateway:8081",
            gateway_internal_service_token="gateway-token",
            confirmation_encryption_key_base64="base64-key",
            rag_storage_backend="s3",
            rag_s3_endpoint_url="https://object-storage.example.com",
            rag_s3_bucket="fitness-agent-knowledge",
            rag_s3_access_key="access-key",
            rag_s3_secret_key="secret-key",
            rag_malware_scanner_backend="clamav",
            rag_ocr_backend="http",
            rag_ocr_endpoint_url="http://ocr-service:8091/v1/parse",
            database_url="postgresql+asyncpg://agent:secret@postgres:5432/fitness_agent",
            redis_url="redis://redis:6379/0",
            gateway_context_signing_algorithm="RS256",
            gateway_context_verification_jwks_url="http://issuer.example/.well-known/jwks.json",
            confirmation_signing_algorithm="RS256",
            confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
        )


def test_production_rejects_local_runtime_defaults() -> None:
    with pytest.raises(ValidationError, match="AGENT_DATABASE_URL 不能使用 localhost"):
        Settings(
            _env_file=None,
            environment="production",
            api_docs_enabled=False,
            metrics_enabled=True,
            otel_enabled=True,
            otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
            llm_api_key="llm-key",
            llm_model="fitness-chat-model",
            gateway_base_url="http://fitness-core-gateway:8081",
            gateway_internal_service_token="gateway-token",
            confirmation_encryption_key_base64="base64-key",
            rag_storage_backend="s3",
            rag_s3_endpoint_url="https://object-storage.example.com",
            rag_s3_bucket="fitness-agent-knowledge",
            rag_s3_access_key="access-key",
            rag_s3_secret_key="secret-key",
            rag_malware_scanner_backend="clamav",
            rag_ocr_backend="http",
            rag_ocr_endpoint_url="http://ocr-service:8091/v1/parse",
            database_url="postgresql+asyncpg://agent:secret@127.0.0.1:5432/fitness_agent",
            redis_url="redis://redis:6379/0",
            gateway_context_signing_algorithm="RS256",
            gateway_context_verification_jwks_url="https://issuer.example/.well-known/jwks.json",
            confirmation_signing_algorithm="RS256",
            confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
        )


def test_production_rejects_trulens_evaluation_capture() -> None:
    with pytest.raises(ValidationError, match="TRULENS_CAPTURE_MODE"):
        Settings(
            _env_file=None,
            environment="production",
            api_docs_enabled=False,
            metrics_enabled=True,
            otel_enabled=True,
            otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
            llm_api_key="llm-key",
            llm_model="fitness-chat-model",
            gateway_base_url="http://fitness-core-gateway:8081",
            gateway_internal_service_token="gateway-token",
            confirmation_encryption_key_base64="base64-key",
            rag_storage_backend="s3",
            rag_s3_endpoint_url="https://object-storage.example.com",
            rag_s3_bucket="fitness-agent-knowledge",
            rag_s3_access_key="access-key",
            rag_s3_secret_key="secret-key",
            rag_malware_scanner_backend="clamav",
            rag_ocr_backend="http",
            rag_ocr_endpoint_url="http://ocr-service:8091/v1/parse",
            database_url="postgresql+asyncpg://agent:secret@postgres:5432/fitness_agent",
            redis_url="redis://redis:6379/0",
            gateway_context_signing_algorithm="RS256",
            gateway_context_verification_jwks_url="https://issuer.example/.well-known/jwks.json",
            confirmation_signing_algorithm="RS256",
            confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
            trulens_capture_mode="evaluation",
        )


def test_production_online_trulens_requires_hmac_secret() -> None:
    with pytest.raises(ValidationError, match="TRULENS_IDENTIFIER_HASH_SECRET"):
        Settings(
            _env_file=None,
            environment="production",
            api_docs_enabled=False,
            metrics_enabled=True,
            otel_enabled=True,
            otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
            llm_api_key="llm-key",
            llm_model="fitness-chat-model",
            gateway_base_url="http://fitness-core-gateway:8081",
            gateway_internal_service_token="gateway-token",
            confirmation_encryption_key_base64="base64-key",
            rag_storage_backend="s3",
            rag_s3_endpoint_url="https://object-storage.example.com",
            rag_s3_bucket="fitness-agent-knowledge",
            rag_s3_access_key="access-key",
            rag_s3_secret_key="secret-key",
            rag_malware_scanner_backend="clamav",
            rag_ocr_backend="http",
            rag_ocr_endpoint_url="http://ocr-service:8091/v1/parse",
            database_url="postgresql+asyncpg://agent:secret@postgres:5432/fitness_agent",
            redis_url="redis://redis:6379/0",
            gateway_context_signing_algorithm="RS256",
            gateway_context_verification_jwks_url="https://issuer.example/.well-known/jwks.json",
            confirmation_signing_algorithm="RS256",
            confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
            trulens_online_export_enabled=True,
        )
