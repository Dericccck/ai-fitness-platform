import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.infrastructure.model_gateway import ModelConfigurationError


@dataclass(frozen=True)
class RerankResult:
    """单条重排结果；index 始终指向调用方传入的原始文档列表。"""

    index: int
    score: float
    document: str


class RerankerClient:
    """生产 Reranker 服务的 HTTP 适配器。

    不同供应商的重排端点并不完全统一，因此 URL、模型、密钥和超时通过环境配置。
    当前适配两种常见响应：``{"results": [...]}`` 和直接数组；每项必须包含原文档
    ``index``，分数可使用 ``relevance_score`` 或 ``score``。新增供应商差异时应继续
    收敛在此适配器中，不能污染 RAG 业务流程。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._local_model: Any = None

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        """根据查询语义重新排序候选文档。

        Reranker 只改变候选顺序，不负责权限过滤。调用前必须根据组织、角色、版本
        和生效时间过滤候选文档；否则仍可能把越权内容发送给模型。
        """

        if not self.settings.reranker_configured:
            raise ModelConfigurationError("Reranker provider is not configured")

        if self.settings.reranker_backend == "local":
            return await asyncio.to_thread(self._rerank_local, query, documents, top_n)

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

        # 使用供应商返回的 index 映射原始文档，避免依赖供应商回传全文。后续接入
        # 具体供应商时还要增加响应 Schema 校验和越界 index 保护。
        return [
            RerankResult(
                index=int(item["index"]),
                score=float(item.get("relevance_score", item.get("score", 0.0))),
                document=documents[int(item["index"])],
            )
            for item in raw_results
        ]

    def _rerank_local(
        self,
        query: str,
        documents: list[str],
        top_n: int | None,
    ) -> list[RerankResult]:
        """在线程池中运行本地 BGE Cross-Encoder，返回原始候选下标。"""

        if self._local_model is None:
            from sentence_transformers import CrossEncoder

            self._local_model = CrossEncoder(self.settings.reranker_model_path, device="cpu")
        scores = self._local_model.predict([(query, document) for document in documents])
        ranked = sorted(
            (
                RerankResult(index=index, score=float(score), document=document)
                for index, (score, document) in enumerate(zip(scores, documents, strict=True))
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        return ranked[: top_n or len(ranked)]
