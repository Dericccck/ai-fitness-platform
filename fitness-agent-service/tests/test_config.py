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


def test_production_requires_asymmetric_authentication_contract() -> None:
    with pytest.raises(ValidationError, match="production authentication contract is incomplete"):
        Settings(_env_file=None, environment="production")


def test_production_authentication_contract_accepts_rs256_and_jwks() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        gateway_context_signing_algorithm="RS256",
        gateway_context_verification_jwks_url="https://issuer.example/.well-known/jwks.json",
        confirmation_signing_algorithm="RS256",
        confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
    )

    assert settings.environment == "production"


def test_production_rejects_non_https_jwks_url() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            gateway_context_signing_algorithm="RS256",
            gateway_context_verification_jwks_url="http://issuer.example/.well-known/jwks.json",
            confirmation_signing_algorithm="RS256",
            confirmation_signing_private_key_pem="-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
        )
