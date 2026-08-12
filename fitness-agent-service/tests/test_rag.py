from datetime import UTC, datetime
from typing import Any

import pytest

from app.infrastructure.reranker import RerankResult
from app.rag.models import KnowledgeChunk, KnowledgeChunkInput, RetrievalScope
from app.rag.service import RagSearchError, RagSearchResult, RagService


class FakeModels:
    def __init__(self) -> None:
        self.embedded: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.append(texts)
        return [[1.0, 0.0] for _ in texts]


class FakeRepository:
    def __init__(self, candidates: list[KnowledgeChunk]) -> None:
        self.candidates = candidates
        self.inserted: tuple[Any, Any] | None = None

    async def insert_chunks(self, chunks: Any, embeddings: Any) -> None:
        self.inserted = (chunks, embeddings)

    async def search_candidates(
        self, embedding: Any, scope: Any, *, limit: int
    ) -> list[KnowledgeChunk]:
        assert embedding == [1.0, 0.0]
        assert scope.organization_ids == frozenset({"org-1"})
        assert scope.roles == frozenset({"STUDENT"})
        assert limit == 20
        return self.candidates


class FakeReranker:
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        assert query == "如何热身"
        assert documents == ["候选 A", "候选 B"]
        assert top_n == 5
        return [RerankResult(index=1, score=0.98, document="候选 B")]


def chunk(identifier: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=identifier,
        document_id="doc-1",
        chunk_index=0,
        content=content,
        source_uri="knowledge://warmup",
        title="热身指南",
        document_type="FITNESS_GUIDE",
        version=1,
        similarity=0.5,
        metadata={},
    )


def scope() -> RetrievalScope:
    return RetrievalScope("user-1", frozenset({"org-1"}), frozenset({"STUDENT"}))


async def test_rag_embeds_queries_filters_candidates_and_uses_reranker() -> None:
    repository = FakeRepository([chunk("a", "候选 A"), chunk("b", "候选 B")])
    models = FakeModels()
    service = RagService(repository, models, FakeReranker())

    result = await service.search("如何热身", scope())

    assert [item.id for item in result.chunks] == ["b"]
    assert result.chunks[0].similarity == 0.98
    assert "来源：knowledge://warmup" in result.as_prompt_context()


async def test_rag_indexing_batches_embeddings_before_persisting() -> None:
    repository = FakeRepository([])
    models = FakeModels()
    service = RagService(repository, models, FakeReranker(), embedding_batch_size=1)
    chunks = [
        KnowledgeChunkInput(
            id="chunk-1",
            document_id="doc-1",
            chunk_index=0,
            content="内容 1",
            content_hash="hash-1",
            organization_id=None,
            owner_user_id=None,
            visibility="GLOBAL",
            allowed_roles=(),
            document_type="FITNESS_GUIDE",
            effective_from=datetime.now(UTC),
            effective_to=None,
            metadata={},
        ),
        KnowledgeChunkInput(
            id="chunk-2",
            document_id="doc-1",
            chunk_index=1,
            content="内容 2",
            content_hash="hash-2",
            organization_id=None,
            owner_user_id=None,
            visibility="GLOBAL",
            allowed_roles=(),
            document_type="FITNESS_GUIDE",
            effective_from=datetime.now(UTC),
            effective_to=None,
            metadata={},
        ),
    ]

    await service.index_chunks(chunks)

    assert models.embedded == [["内容 1"], ["内容 2"]]
    assert repository.inserted is not None
    assert repository.inserted[1] == [[1.0, 0.0], [1.0, 0.0]]


async def test_rag_rejects_invalid_reranker_index() -> None:
    class InvalidReranker:
        async def rerank(
            self, query: str, documents: list[str], *, top_n: int | None = None
        ) -> list[RerankResult]:
            return [RerankResult(index=99, score=1.0, document="invalid")]

    service = RagService(FakeRepository([chunk("a", "候选 A")]), FakeModels(), InvalidReranker())

    with pytest.raises(RagSearchError, match="invalid"):
        await service.search("如何热身", scope())


async def test_rag_rejects_unexpected_embedding_dimension() -> None:
    service = RagService(
        FakeRepository([]), FakeModels(), FakeReranker(), embedding_dimensions=1536
    )

    with pytest.raises(RagSearchError, match="dimension"):
        await service.search("如何热身", scope())


def test_rag_prompt_context_expands_each_parent_only_once() -> None:
    first = chunk("a", "命中片段 A")
    second = chunk("b", "命中片段 B")
    first = KnowledgeChunk(
        **{**first.__dict__, "parent_id": "parent-1", "parent_content": "完整章节内容"}
    )
    second = KnowledgeChunk(
        **{**second.__dict__, "parent_id": "parent-1", "parent_content": "完整章节内容"}
    )

    context = RagSearchResult((first, second)).as_prompt_context()

    assert context.count("完整章节内容") == 1
    assert "命中片段 B" in context
