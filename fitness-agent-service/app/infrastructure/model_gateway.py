from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings


class ModelConfigurationError(RuntimeError):
    """Raised when a real model provider has not been configured."""


class ModelGateway:
    """Single entry point for LLM and embedding providers.

    Business agents must depend on this gateway instead of constructing SDK clients
    directly. This keeps provider changes, timeouts, tracing, and retry policy in one
    place and prevents accidental local/mock fallbacks in production.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._llm = AsyncOpenAI(
            api_key=settings.llm_api_key or "not-configured",
            base_url=settings.llm_base_url,
        )
        self._embedding = AsyncOpenAI(
            api_key=settings.embedding_effective_api_key or "not-configured",
            base_url=settings.embedding_base_url,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM provider is not configured")

        response = await self._llm.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.embedding_configured:
            raise ModelConfigurationError("Embedding provider is not configured")

        response = await self._embedding.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    async def close(self) -> None:
        await self._llm.close()
        await self._embedding.close()


def redact_provider_config(settings: Settings) -> dict[str, Any]:
    """Expose safe provider state for health diagnostics, never credentials."""

    return {
        "llm": {"configured": settings.llm_configured, "model": settings.llm_model},
        "embedding": {
            "configured": settings.embedding_configured,
            "model": settings.embedding_model,
        },
        "reranker": {
            "configured": settings.reranker_configured,
            "model": settings.reranker_model,
        },
    }
