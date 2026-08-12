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
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "fitness-chat-model"
    assert settings.otel_configured is False
    assert settings.gateway_configured is False


def test_deepseek_environment_names_are_supported(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key == "deepseek-key"
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_base_url == "https://api.deepseek.com"
