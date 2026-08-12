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
    assert settings.otel_configured is False
    assert settings.gateway_configured is False
