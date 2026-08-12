from datetime import UTC, datetime
from typing import Any

import pytest

from app.rag.ingestion import (
    DocumentIngestionService,
    IngestionConflictError,
    IngestionRequest,
    chunk_markdown,
    clean_markdown,
    content_checksum,
)
from app.rag.models import KnowledgeDocumentInput, KnowledgeDocumentSnapshot


class FakeRepository:
    def __init__(self, current: KnowledgeDocumentSnapshot | None = None) -> None:
        self.current = current

    async def get_current_document(self, source_uri: str) -> KnowledgeDocumentSnapshot | None:
        return self.current


class FakeRagService:
    def __init__(self) -> None:
        self.calls: list[tuple[KnowledgeDocumentInput, Any, Any]] = []

    async def index_document(
        self, document: KnowledgeDocumentInput, chunks: Any, *, parents: Any = ()
    ) -> None:
        self.calls.append((document, chunks, parents))


def request(content: str, *, version: int = 1) -> IngestionRequest:
    return IngestionRequest(
        source_uri="knowledge://fitness/warmup.md",
        title="热身指南",
        document_type="FITNESS_GUIDE",
        raw_content=content,
        organization_id=None,
        owner_user_id=None,
        visibility="GLOBAL",
        allowed_roles=(),
        version=version,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_clean_markdown_removes_front_matter_and_normalizes_blank_lines() -> None:
    cleaned = clean_markdown("\ufeff---\ntitle: old\n---\r\n# 热身\r\n\r\n\r\n准备身体。")

    assert cleaned == "# 热身\n\n准备身体。"


def test_chunk_markdown_preserves_heading_context_and_bounds_chunks() -> None:
    content = clean_markdown(
        "# 热身\n\n" + "准备身体。" * 30 + "\n\n## 下肢\n\n深蹲前先活动髋关节。"
    )

    chunks = chunk_markdown(content, max_chunk_chars=100, overlap_chars=10)

    assert chunks
    assert all(len(chunk.content) <= 100 for chunk in chunks)
    assert any(chunk.heading_path == ("热身",) for chunk in chunks)
    assert any(chunk.heading_path == ("热身", "下肢") for chunk in chunks)


async def test_ingestion_skips_unchanged_content_without_calling_embedding() -> None:
    raw_content = "# 热身\n\n准备身体。"
    current = KnowledgeDocumentSnapshot(
        id="existing",
        source_uri="knowledge://fitness/warmup.md",
        checksum=content_checksum(clean_markdown(raw_content)),
        version=3,
        status="PUBLISHED",
    )
    repository = FakeRepository(current)
    rag = FakeRagService()
    service = DocumentIngestionService(repository, rag)

    result = await service.ingest(request(raw_content, version=4))

    assert result.status == "SKIPPED_UNCHANGED"
    assert result.document_id == "existing"
    assert result.version == 3
    assert rag.calls == []


async def test_ingestion_indexes_new_version_with_deterministic_chunks() -> None:
    repository = FakeRepository()
    rag = FakeRagService()
    service = DocumentIngestionService(repository, rag, max_chunk_chars=200, overlap_chars=20)

    result = await service.ingest(request("# 热身\n\n准备身体。", version=2))

    assert result.status == "INDEXED"
    assert result.version == 2
    assert result.chunk_count == 1
    assert len(rag.calls) == 1
    document, chunks, parents = rag.calls[0]
    assert document.version == 2
    assert document.checksum == result.checksum
    assert chunks[0].document_id == result.document_id
    assert chunks[0].metadata["heading_path"] == ["热身"]
    assert chunks[0].parent_id == parents[0].id
    assert parents[0].content == "热身\n准备身体。"
    assert chunks[0].id


def test_markdown_table_chunks_repeat_header_and_record_row_range() -> None:
    content = clean_markdown(
        "# 训练计划\n\n"
        "| 动作 | 组数 | 次数 |\n"
        "|---|---:|---:|\n"
        "| 深蹲 | 4 | 12 |\n"
        "| 卧推 | 3 | 10 |\n"
        "| 硬拉 | 3 | 8 |"
    )

    chunks = chunk_markdown(content, max_chunk_chars=65, overlap_chars=0)

    assert len(chunks) >= 2
    assert all("| 动作 | 组数 | 次数 |" in item.content for item in chunks)
    assert all(item.table_index == 0 for item in chunks)
    assert chunks[0].row_start == 1
    assert chunks[-1].row_end == 3


async def test_ingestion_rejects_non_increasing_changed_version() -> None:
    repository = FakeRepository(
        KnowledgeDocumentSnapshot(
            id="existing",
            source_uri="knowledge://fitness/warmup.md",
            checksum="different",
            version=3,
            status="PUBLISHED",
        )
    )
    service = DocumentIngestionService(repository, FakeRagService())

    with pytest.raises(IngestionConflictError):
        await service.ingest(request("# 新内容\n\n准备身体。", version=3))
