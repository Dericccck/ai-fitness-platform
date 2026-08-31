from datetime import UTC, datetime
from typing import Any

import pytest

from app.rag.formats import ParsedBlock, ParsedDocument, PdfPageProfile
from app.rag.ingestion import (
    DocumentIngestionService,
    DocumentPublicationBlocked,
    IngestionConflictError,
    IngestionRequest,
    chunk_markdown,
    chunk_parsed_blocks,
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


def test_oversized_table_header_keeps_table_provenance() -> None:
    content = clean_markdown(
        "# 训练计划\n\n| 非常长的动作名称和说明 | 非常长的组数说明 |\n|---|---|\n| 深蹲 | 4 |"
    )

    chunks = chunk_markdown(content, max_chunk_chars=40, overlap_chars=0)

    assert chunks
    assert all(item.table_index == 0 for item in chunks)


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


def test_chunk_parsed_blocks_carries_page_sheet_and_table_metadata() -> None:
    drafts = chunk_parsed_blocks(
        [
            ParsedBlock(
                kind="TABLE",
                content="| 动作 | 组数 |\n|---|---|\n| 深蹲 | 4 |",
                heading_path=("初级计划",),
                source_sheet="第 1 周",
                table_index=2,
                metadata={"parser": "openpyxl"},
            )
        ],
        max_chunk_chars=120,
        overlap_chars=0,
    )

    assert drafts[0].source_sheet == "第 1 周"
    assert drafts[0].table_index == 2
    assert drafts[0].metadata == {"parser": "openpyxl"}


async def test_ingest_file_indexes_xlsx_with_source_metadata() -> None:
    from io import BytesIO

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "第 1 周"
    sheet.append(["动作", "组数"])
    sheet.append(["深蹲", 4])
    payload = BytesIO()
    workbook.save(payload)

    repository = FakeRepository()
    rag = FakeRagService()
    service = DocumentIngestionService(repository, rag)
    result = await service.ingest_file(
        request("", version=1),
        file_name="plan.xlsx",
        content=payload.getvalue(),
    )

    assert result.status == "INDEXED"
    document, chunks, parents = rag.calls[0]
    assert document.checksum == result.checksum
    assert chunks[0].metadata["source_sheet"] == "第 1 周"
    assert chunks[0].metadata["parser"] == "openpyxl"
    assert parents[0].metadata["source_sheet"] == "第 1 周"


class RoutedParserRegistry:
    def __init__(self, route: str) -> None:
        self.route = route

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        return ParsedDocument(
            blocks=(ParsedBlock(kind="TEXT", content="深蹲动作说明。", source_page=1),),
            media_type="application/pdf",
            page_profiles=(
                PdfPageProfile(
                    1,
                    1,
                    0.8,
                    8,
                    0.04,
                    0,
                    1,
                    self.route,  # type: ignore[arg-type]
                ),
            ),
        )


@pytest.mark.parametrize(
    "route",
    ["OCR_REQUIRED", "VISUAL_REVIEW_REQUIRED", "OCR_AND_VISUAL_REVIEW_REQUIRED"],
)
async def test_ingest_file_blocks_unresolved_pdf_pages_before_embedding(route: str) -> None:
    rag = FakeRagService()
    service = DocumentIngestionService(
        FakeRepository(),
        rag,
        parser_registry=RoutedParserRegistry(route),  # type: ignore[arg-type]
    )

    with pytest.raises(DocumentPublicationBlocked, match=route):
        await service.ingest_file(
            request("", version=1),
            file_name="exercise.pdf",
            content=b"pdf",
        )

    assert rag.calls == []


async def test_publication_credential_only_releases_pure_visual_review() -> None:
    visual_rag = FakeRagService()
    visual_service = DocumentIngestionService(
        FakeRepository(),
        visual_rag,
        parser_registry=RoutedParserRegistry("VISUAL_REVIEW_REQUIRED"),  # type: ignore[arg-type]
    )

    result = await visual_service.ingest_file(
        request("", version=1),
        file_name="exercise.pdf",
        content=b"pdf",
        reviewed_visual_pages=(1,),
    )
    assert result.status == "INDEXED"

    # OCR_AND_VISUAL 仍缺少可索引文字，专业审核只能确认动作风险，不能替代 OCR。
    ocr_service = DocumentIngestionService(
        FakeRepository(),
        FakeRagService(),
        parser_registry=RoutedParserRegistry("OCR_AND_VISUAL_REVIEW_REQUIRED"),  # type: ignore[arg-type]
    )
    with pytest.raises(DocumentPublicationBlocked, match="OCR_AND_VISUAL"):
        await ocr_service.ingest_file(
            request("", version=1),
            file_name="exercise.pdf",
            content=b"pdf",
            reviewed_visual_pages=(1,),
        )
