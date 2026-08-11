from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.infrastructure.model_gateway import ModelConfigurationError


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float
    document: str


class RerankerClient:
    """HTTP adapter for a production reranker provider.

    The endpoint is intentionally configurable because reranker APIs differ across
    providers. The expected response is either {"results": [...]} or a bare list,
    with each item containing index and relevance_score/score.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        if not self.settings.reranker_configured:
            raise ModelConfigurationError("Reranker provider is not configured")

        headers = {"Content-Type": "application/json"}
        if self.settings.reranker_api_key:
            headers["Authorization"] = f"Bearer {self.settings.reranker_api_key}"
        payload = {
            "model": self.settings.reranker_model,
            "query": query,
            "documents": documents,
            "top_n": top_n or len(documents),
        }
        async with httpx.AsyncClient(timeout=self.settings.reranker_timeout_seconds) as client:
            response = await client.post(self.settings.reranker_url, json=payload, headers=headers)
            response.raise_for_status()

        raw_results = response.json()
        if isinstance(raw_results, dict):
            raw_results = raw_results.get("results", [])

        return [
            RerankResult(
                index=int(item["index"]),
                score=float(item.get("relevance_score", item.get("score", 0.0))),
                document=documents[int(item["index"])],
            )
            for item in raw_results
        ]
