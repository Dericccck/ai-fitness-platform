"""文档清洗、语义分块和增量 RAG 索引。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .formats import DocumentParserRegistry, ParsedBlock, ParsedDocument
from .models import (
    KnowledgeChunkInput,
    KnowledgeDocumentInput,
    KnowledgeParentInput,
)
from .repository import KnowledgeRepository
from .service import RagSearchError, RagService
from .text import clean_markdown

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_END = re.compile(r"(?<=[。！？.!?])\s+")


class IngestionConflictError(RuntimeError):
    """来源版本将回退，或复用了冲突的版本号。"""


class DocumentPublicationBlocked(RuntimeError):
    """解析结果仍需要 OCR 或专业视觉审核，禁止生成生产 Embedding。"""


# INDEXED 表示已完成清洗、切片、Embedding 和原子发布；SKIPPED_UNCHANGED 表示 checksum
# 未变化而复用已有版本，不能误报为重新索引成功。
IngestionResultStatus = Literal["INDEXED", "SKIPPED_UNCHANGED"]


@dataclass(frozen=True)
class IngestionRequest:
    """管理员工作流或来源连接器提供的可信文档元数据。"""

    source_uri: str
    title: str
    document_type: str
    raw_content: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    allowed_roles: tuple[str, ...]
    version: int
    effective_from: datetime
    effective_to: datetime | None = None
    status: str = "PUBLISHED"


@dataclass(frozen=True)
class ChunkDraft:
    """子节点内容，以及包含完整章节的父节点上下文。"""

    content: str
    heading_path: tuple[str, ...]
    parent_content: str
    table_index: int | None = None
    row_start: int | None = None
    row_end: int | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class IngestionResult:
    """索引任务或管理员审计记录使用的稳定结果。"""

    status: IngestionResultStatus
    document_id: str
    checksum: str
    version: int
    chunk_count: int


class DocumentIngestionService:
    """将可信文档文本转换为增量索引的知识版本。

    Embedding 之前先完成解析和分块。校验和基于规范化内容计算，因此只有空白变化
    不会消耗模型额度，也不会产生没有必要的新版本。仓储层会在同一个事务中归档
    之前已发布的来源版本，并发布替换版本。
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        rag_service: RagService,
        *,
        max_chunk_chars: int = 1200,
        overlap_chars: int = 120,
        parser_registry: DocumentParserRegistry | None = None,
    ) -> None:
        if max_chunk_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chunk_chars:
            raise ValueError("invalid chunking limits")
        self.repository = repository
        self.rag_service = rag_service
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars
        self.parser_registry = parser_registry or DocumentParserRegistry()

    async def ingest(self, request: IngestionRequest, *, force: bool = False) -> IngestionResult:
        """清洗、分块、去重、生成 Embedding，并原子发布文档。"""

        cleaned = clean_markdown(request.raw_content)
        drafts = chunk_markdown(
            cleaned,
            max_chunk_chars=self.max_chunk_chars,
            overlap_chars=self.overlap_chars,
        )
        return await self._publish(
            request, checksum=content_checksum(cleaned), drafts=drafts, force=force
        )

    async def ingest_file(
        self,
        request: IngestionRequest,
        *,
        file_name: str,
        content: bytes,
        force: bool = False,
        reviewed_visual_pages: tuple[int, ...] = (),
    ) -> IngestionResult:
        """解析二进制来源、保留坐标，再复用相同的发布路径。"""

        parsed = self.parser_registry.parse(content, file_name=file_name)
        _require_publishable_document(parsed, reviewed_visual_pages=reviewed_visual_pages)
        drafts = chunk_parsed_blocks(
            parsed.blocks,
            max_chunk_chars=self.max_chunk_chars,
            overlap_chars=self.overlap_chars,
        )
        checksum_material = "\n\n".join(
            json.dumps(
                {
                    "kind": block.kind,
                    "content": block.content,
                    "heading_path": block.heading_path,
                    "source_page": block.source_page,
                    "source_sheet": block.source_sheet,
                    "table_index": block.table_index,
                    "row_start": block.row_start,
                    "row_end": block.row_end,
                    "metadata": block.metadata,
                    # 父节点上下文也是索引内容的一部分。PDF 解析策略调整后，即使
                    # 子块正文不变，只要章节级父上下文变化，也必须生成新校验和，
                    # 这样增量入库才不会错误地复用旧的父节点。
                    "parent_content": block.parent_content,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for block in parsed.blocks
        )
        return await self._publish(
            request,
            checksum=content_checksum(checksum_material),
            drafts=drafts,
            force=force,
        )

    async def _publish(
        self,
        request: IngestionRequest,
        *,
        checksum: str,
        drafts: Sequence[ChunkDraft],
        force: bool = False,
    ) -> IngestionResult:
        """校验版本，并原子发布标准化的子节点/父节点。"""

        current = await self.repository.get_current_document(request.source_uri)
        if current is not None:
            if current.checksum == checksum and not force:
                return IngestionResult(
                    status="SKIPPED_UNCHANGED",
                    document_id=current.id,
                    checksum=checksum,
                    version=current.version,
                    chunk_count=0,
                )
            if request.version < current.version or (
                request.version == current.version and current.checksum != checksum
            ):
                raise IngestionConflictError(
                    f"document version {request.version} is not newer than {current.version}"
                )

        if not drafts:
            raise RagSearchError("document produced no indexable chunks")

        document_id = versioned_document_id(request.source_uri, request.version)
        document = KnowledgeDocumentInput(
            id=document_id,
            organization_id=request.organization_id,
            title=request.title,
            source_uri=request.source_uri,
            document_type=request.document_type,
            visibility=request.visibility,
            applicable_roles=request.allowed_roles,
            version=request.version,
            status=request.status,
            checksum=checksum,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
        )
        parents: list[KnowledgeParentInput] = []
        parent_ids: dict[tuple[Any, ...], str] = {}
        chunk_inputs: list[KnowledgeChunkInput] = []
        for index, draft in enumerate(drafts):
            parent_key = (
                draft.heading_path,
                draft.parent_content,
                draft.source_page,
                draft.source_sheet,
                draft.table_index,
            )
            parent_id = parent_ids.get(parent_key)
            if parent_id is None:
                parent_id = parent_node_id(document_id, len(parents), draft.parent_content)
                parent_ids[parent_key] = parent_id
                parents.append(
                    KnowledgeParentInput(
                        id=parent_id,
                        document_id=document_id,
                        content=draft.parent_content,
                        section_path=draft.heading_path,
                        source_page=draft.source_page,
                        table_index=draft.table_index,
                        row_start=draft.row_start,
                        row_end=draft.row_end,
                        metadata=_source_metadata(draft),
                    )
                )
            chunk_inputs.append(
                KnowledgeChunkInput(
                    id=chunk_id(document_id, index, draft.content),
                    document_id=document_id,
                    chunk_index=index,
                    content=draft.content,
                    content_hash=content_checksum(draft.content),
                    organization_id=request.organization_id,
                    owner_user_id=request.owner_user_id,
                    visibility=request.visibility,
                    allowed_roles=request.allowed_roles,
                    document_type=request.document_type,
                    effective_from=request.effective_from,
                    effective_to=request.effective_to,
                    metadata=_source_metadata(draft),
                    parent_id=parent_id,
                )
            )
        chunks = tuple(chunk_inputs)
        await self.rag_service.index_document(document, chunks, parents=tuple(parents))
        return IngestionResult(
            status="INDEXED",
            document_id=document_id,
            checksum=checksum,
            version=request.version,
            chunk_count=len(chunks),
        )


def chunk_parsed_blocks(
    blocks: Sequence[ParsedBlock],
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """切分解析器输出，并将页码/工作表/表格来源信息复制到每个子节点。"""

    drafts: list[ChunkDraft] = []
    for block in blocks:
        block_drafts = _split_block(
            block.content,
            block.heading_path,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
            table_index=block.table_index if block.kind == "TABLE" else None,
        )
        for draft in block_drafts:
            drafts.append(
                ChunkDraft(
                    content=draft.content,
                    heading_path=draft.heading_path,
                    # PDF 解析器会额外提供章节级父上下文；Markdown/DOCX/XLSX 没有
                    # 提供时仍使用当前块内容，保持原有格式的兼容行为。
                    parent_content=block.parent_content or draft.parent_content,
                    table_index=draft.table_index,
                    row_start=draft.row_start,
                    row_end=draft.row_end,
                    source_page=block.source_page,
                    source_sheet=block.source_sheet,
                    metadata=block.metadata,
                )
            )
    return drafts


def _source_metadata(draft: ChunkDraft) -> dict[str, Any]:
    """构建可审计元数据，但不把授权决定放入 JSON。"""

    metadata: dict[str, Any] = {
        "heading_path": list(draft.heading_path),
    }
    if draft.source_page is not None:
        metadata["source_page"] = draft.source_page
    if draft.source_sheet is not None:
        metadata["source_sheet"] = draft.source_sheet
    if draft.table_index is not None:
        metadata["table_index"] = draft.table_index
    if draft.row_start is not None:
        metadata["row_start"] = draft.row_start
    if draft.row_end is not None:
        metadata["row_end"] = draft.row_end
    if draft.metadata:
        metadata.update(draft.metadata)
    return metadata


def chunk_markdown(
    cleaned_content: str,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """按标题/段落切分 Markdown，再按句子切分过大的内容块。"""

    if max_chunk_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chunk_chars:
        raise ValueError("invalid chunking limits")

    heading_path: list[str] = []
    sections: list[tuple[tuple[str, ...], str]] = []
    current_lines: list[str] = []
    current_path: tuple[str, ...] = ()

    def flush_section() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_path, body))
        current_lines.clear()

    for line in cleaned_content.split("\n") + [""]:
        heading = _HEADING.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(heading.group(2).strip())
            current_path = tuple(heading_path)
            continue
        current_lines.append(line)
    flush_section()

    drafts: list[ChunkDraft] = []
    table_index = 0
    for section_path, body in sections:
        current_table_index = table_index if _is_markdown_table(body) else None
        if current_table_index is not None:
            table_index += 1
        drafts.extend(
            _split_block(
                body,
                section_path,
                max_chunk_chars=max_chunk_chars,
                overlap_chars=overlap_chars,
                table_index=current_table_index,
            )
        )
    return drafts


def _split_block(
    body: str,
    heading_path: tuple[str, ...],
    *,
    max_chunk_chars: int,
    overlap_chars: int,
    table_index: int | None,
) -> list[ChunkDraft]:
    """保持短内容块完整，并切分长文本，避免逐词截断。"""

    prefix = f"{' / '.join(heading_path)}\n" if heading_path else ""
    parent_content = prefix + body
    if table_index is not None:
        return _split_table_block(
            body,
            heading_path,
            parent_content=parent_content,
            prefix=prefix,
            table_index=table_index,
            max_chunk_chars=max_chunk_chars,
        )
    available = max_chunk_chars - len(prefix)
    if len(body) <= available:
        return [ChunkDraft(prefix + body, heading_path, parent_content)]

    sentences = [part.strip() for part in _SENTENCE_END.split(body) if part.strip()]
    if not sentences:
        sentences = [body]
    result: list[ChunkDraft] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > available:
            if current:
                result.append(ChunkDraft(prefix + current, heading_path, parent_content))
                current = ""
            result.extend(
                _split_long_text(
                    sentence,
                    heading_path,
                    available=available,
                    overlap_chars=overlap_chars,
                    prefix=prefix,
                    parent_content=parent_content,
                )
            )
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > available:
            result.append(ChunkDraft(prefix + current, heading_path, parent_content))
            current = _overlap_tail(current, overlap_chars)
        current = f"{current} {sentence}".strip()
    if current:
        result.append(ChunkDraft(prefix + current, heading_path, parent_content))
    return result


def _split_table_block(
    body: str,
    heading_path: tuple[str, ...],
    *,
    parent_content: str,
    prefix: str,
    table_index: int,
    max_chunk_chars: int,
) -> list[ChunkDraft]:
    """按行组切分 Markdown 表格，并重复表头。"""

    lines = [line.strip() for line in body.split("\n") if line.strip()]
    separator_index = next(
        (index for index, line in enumerate(lines) if _is_table_separator(line)),
        None,
    )
    if separator_index is None or separator_index == 0:
        return [ChunkDraft(prefix + body, heading_path, parent_content, table_index)]

    header = "\n".join(lines[: separator_index + 1])
    rows = lines[separator_index + 1 :]
    available = max_chunk_chars - len(prefix)
    if len(header) > available:
        return _split_long_text(
            body,
            heading_path,
            available=available,
            overlap_chars=0,
            prefix=prefix,
            parent_content=parent_content,
            table_index=table_index,
        )

    result: list[ChunkDraft] = []
    current_rows: list[str] = []
    row_start = 1
    for row_number, row in enumerate(rows, start=1):
        candidate = "\n".join([header, *current_rows, row])
        if current_rows and len(candidate) > available:
            result.append(
                ChunkDraft(
                    prefix + "\n".join([header, *current_rows]),
                    heading_path,
                    parent_content,
                    table_index,
                    row_start,
                    row_number - 1,
                )
            )
            current_rows = []
            row_start = row_number
        current_rows.append(row)
    if current_rows:
        result.append(
            ChunkDraft(
                prefix + "\n".join([header, *current_rows]),
                heading_path,
                parent_content,
                table_index,
                row_start,
                len(rows),
            )
        )
    return result


def _is_markdown_table(body: str) -> bool:
    """识别 Markdown 表格，不把任意管道分隔文本误判为表格。"""

    lines = [line.strip() for line in body.split("\n") if line.strip()]
    return len(lines) >= 2 and any(_is_table_separator(line) for line in lines)


def _is_table_separator(line: str) -> bool:
    """识别表头下方使用的 Markdown 分隔行。"""

    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(bool(re.fullmatch(r":?-{3,}:?", cell)) for cell in cells)


def _split_long_text(
    text: str,
    heading_path: tuple[str, ...],
    *,
    available: int,
    overlap_chars: int,
    prefix: str,
    parent_content: str,
    table_index: int | None = None,
) -> list[ChunkDraft]:
    """限制复制表格、超长 URL 等异常段落的长度。"""

    result: list[ChunkDraft] = []
    start = 0
    while start < len(text):
        end = min(start + available, len(text))
        piece = text[start:end].strip()
        if piece:
            result.append(
                ChunkDraft(
                    prefix + piece,
                    heading_path,
                    parent_content,
                    table_index=table_index,
                )
            )
        if end == len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return result


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """使用有界尾部作为下一个内容块的上下文，同时不超过长度限制。"""

    if overlap_chars == 0:
        return ""
    return text[-overlap_chars:].lstrip()


def content_checksum(content: str) -> str:
    """返回稳定的 SHA-256 校验和，用于去重和审计记录。"""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _require_publishable_document(
    parsed: ParsedDocument, *, reviewed_visual_pages: tuple[int, ...] = ()
) -> None:
    """在模型调用前执行 fail-closed 页面路由门禁。

    ``reviewed_visual_pages`` 只由已核验数据库发布凭证传入，可解除纯视觉人工审核。
    OCR_REQUIRED 和 OCR_AND_VISUAL_REVIEW_REQUIRED 仍然阻断，人工审核不能补造缺失正文。
    """

    route_pages: dict[str, list[int]] = {}
    for profile in parsed.page_profiles:
        if profile.route == "VISUAL_REVIEW_REQUIRED" and profile.page_number in set(
            reviewed_visual_pages
        ):
            continue
        if profile.route != "NORMAL":
            route_pages.setdefault(profile.route, []).append(profile.page_number)
    # 保留原始组合状态，而不是拆成两个模糊错误。审核报告和告警可以据此区分
    # “只缺文字”“只需动作审核”和“两者都需要”，也便于稳定聚合指标。
    reasons = [f"{route} pages={pages}" for route, pages in sorted(route_pages.items())]
    if reasons:
        raise DocumentPublicationBlocked("; ".join(reasons))


def versioned_document_id(source_uri: str, version: int) -> str:
    """生成不透明 ID，避免来源 URL 直接成为数据库标识。"""

    return hashlib.sha256(f"{source_uri}\n{version}".encode()).hexdigest()


def chunk_id(document_id: str, index: int, content: str) -> str:
    """生成确定性 ID，保证重试时内容块写入安全。"""

    return hashlib.sha256(
        f"{document_id}\n{index}\n{content_checksum(content)}".encode()
    ).hexdigest()


def parent_node_id(document_id: str, index: int, content: str) -> str:
    """为子节点召回后扩展的上下文节点生成稳定 ID。"""

    return hashlib.sha256(
        f"parent\n{document_id}\n{index}\n{content_checksum(content)}".encode()
    ).hexdigest()
