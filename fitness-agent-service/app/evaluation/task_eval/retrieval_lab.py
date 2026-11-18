"""Repeatable RAG component lab using generated documents and substitute dependencies.

The in-memory repository performs text/vector matching at query time. Its ACL,
embedding and reranker are test substitutes, not production or model validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from app.infrastructure.reranker import RerankResult
from app.rag.models import KnowledgeChunk, RetrievalScope
from app.rag.service import RagService

from .component_retrieval import RetrievalCase, evaluate_retrieval


def _features(text: str) -> list[float]:
    text = "".join(text.lower().split())
    features = [0.0] * 512
    for i in range(max(len(text) - 1, 0)):
        index = int.from_bytes(hashlib.sha256(text[i : i + 2].encode()).digest()[:4], "big") % 512
        features[index] += 1.0
    norm = math.sqrt(sum(value * value for value in features)) or 1.0
    return [value / norm for value in features]


def _similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


@dataclass(frozen=True)
class _Document:
    id: str
    text: str
    organization: str
    role: str


_DOCUMENTS = (
    _Document(
        "warmup", "力量训练前如何热身：先做轻度有氧，再进行针对动作的渐进热身。", "org-a", "STUDENT"
    ),
    _Document("breathing", "深蹲上推时怎么呼吸：上推时呼气，下蹲时吸气。", "org-a", "STUDENT"),
    _Document("foreign", "机构内部秘密训练方案：仅供其他机构成员阅读。", "org-b", "STUDENT"),
    _Document("finance", "机构内部财务秘密训练方案：仅供财务人员阅读。", "org-a", "FINANCE"),
)
_SCOPE = RetrievalScope("test-student", frozenset({"org-a"}), frozenset({"STUDENT"}))
CASES = [
    RetrievalCase(
        "local-warmup", "力量训练前如何热身", _SCOPE, ("warmup",), ("foreign", "finance"), k=2
    ),
    RetrievalCase(
        "local-breathing", "深蹲上推时怎么呼吸", _SCOPE, ("breathing",), ("foreign", "finance"), k=2
    ),
    RetrievalCase(
        "local-unanswerable", "量子飞船推进剂", _SCOPE, (), ("foreign", "finance"), answerable=False
    ),
    RetrievalCase(
        "local-cross-org-role",
        "机构内部秘密训练方案",
        _SCOPE,
        (),
        ("foreign", "finance"),
        answerable=False,
    ),
]


class _Models:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_features(text) for text in texts]


class _Repository:
    def __init__(self, *, bypass_acl: bool = False) -> None:
        self.bypass_acl = bypass_acl

    def _accessible(self, scope: RetrievalScope) -> list[_Document]:
        return [
            doc
            for doc in _DOCUMENTS
            if self.bypass_acl
            or (doc.organization in scope.organization_ids and doc.role in scope.roles)
        ]

    @staticmethod
    def _chunk(doc: _Document, score: float) -> KnowledgeChunk:
        return KnowledgeChunk(
            doc.id, doc.id, 0, doc.text, f"fixture://{doc.id}", doc.id, "LAB", 1, score, {}
        )

    async def search_candidates(
        self, embedding: list[float], scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        scored = [
            (doc, _similarity(embedding, _features(doc.text))) for doc in self._accessible(scope)
        ]
        return [
            self._chunk(doc, score)
            for doc, score in sorted(scored, key=lambda item: (-item[1], item[0].id))
            if score > 0
        ][:limit]

    async def search_keyword_candidates(
        self, query: str, scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        # This deterministic lab uses the same bag-of-bigrams scoring for lexical recall.
        return await self.search_candidates(_features(query), scope, limit=limit)


class _Reranker:
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        scored = [
            RerankResult(i, _similarity(_features(query), _features(doc)), doc)
            for i, doc in enumerate(documents)
        ]
        return sorted(scored, key=lambda item: (-item.score, item.index))[:top_n]


async def run_lab(*, bypass_acl: bool = False) -> dict[str, Any]:
    service = RagService(_Repository(bypass_acl=bypass_acl), _Models(), _Reranker(), top_k=2)  # type: ignore[arg-type]
    result = await evaluate_retrieval(service, CASES)
    result["dependencies"] = "local_simulated_acl_hash_bigrams_and_reranker"
    result["corpus"] = "four_synthetic_documents"
    return result


def main() -> int:
    result = asyncio.run(run_lab())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        1
        if any(
            row["retrieval"]["status"] in {"FAIL", "ERROR"} or row["acl"]["status"] == "FAIL"
            for row in result["cases"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
