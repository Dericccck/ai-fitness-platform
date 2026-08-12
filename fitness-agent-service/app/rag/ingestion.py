"""Document cleaning, semantic chunking, and incremental RAG indexing."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .models import (
    KnowledgeChunkInput,
    KnowledgeDocumentInput,
)
from .repository import KnowledgeRepository
from .service import RagSearchError, RagService

_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_END = re.compile(r"(?<=[。！？.!?])\s+")


class IngestionConflictError(RuntimeError):
    """The source version would move backwards or reuse a conflicting version."""


@dataclass(frozen=True)
class IngestionRequest:
    """Trusted document metadata supplied by an admin workflow or source connector."""

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
    """Clean chunk content and the heading context used to explain its scope."""

    content: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True)
class IngestionResult:
    """Stable result for an indexing job or an admin audit record."""

    status: Literal["INDEXED", "SKIPPED_UNCHANGED"]
    document_id: str
    checksum: str
    version: int
    chunk_count: int


class DocumentIngestionService:
    """Turn trusted document text into an incrementally indexed knowledge version.

    Parsing and chunking happen before Embedding. The checksum is calculated from
    normalized content, so whitespace-only edits do not consume model quota or
    create an unnecessary new version. The repository then archives the prior
    published source version in the same transaction as the replacement.
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        rag_service: RagService,
        *,
        max_chunk_chars: int = 1200,
        overlap_chars: int = 120,
    ) -> None:
        if max_chunk_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chunk_chars:
            raise ValueError("invalid chunking limits")
        self.repository = repository
        self.rag_service = rag_service
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars

    async def ingest(self, request: IngestionRequest) -> IngestionResult:
        """Clean, chunk, deduplicate, embed, and atomically publish a document."""

        cleaned = clean_markdown(request.raw_content)
        checksum = content_checksum(cleaned)
        current = await self.repository.get_current_document(request.source_uri)
        if current is not None:
            if current.checksum == checksum:
                return IngestionResult(
                    status="SKIPPED_UNCHANGED",
                    document_id=current.id,
                    checksum=checksum,
                    version=current.version,
                    chunk_count=0,
                )
            if request.version <= current.version:
                raise IngestionConflictError(
                    f"document version {request.version} is not newer than {current.version}"
                )

        drafts = chunk_markdown(
            cleaned,
            max_chunk_chars=self.max_chunk_chars,
            overlap_chars=self.overlap_chars,
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
        chunks = tuple(
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
                metadata={"heading_path": list(draft.heading_path)},
            )
            for index, draft in enumerate(drafts)
        )
        await self.rag_service.index_document(document, chunks)
        return IngestionResult(
            status="INDEXED",
            document_id=document_id,
            checksum=checksum,
            version=request.version,
            chunk_count=len(chunks),
        )


def clean_markdown(raw_content: str) -> str:
    """Normalize Markdown while preserving headings, bullets, and paragraph boundaries."""

    normalized = unicodedata.normalize("NFKC", raw_content).replace("\r\n", "\n")
    normalized = normalized.replace("\r", "\n").lstrip("\ufeff")
    normalized = _FRONT_MATTER.sub("", normalized, count=1)
    lines: list[str] = []
    previous_blank = False
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError("document content must not be empty")
    return cleaned


def chunk_markdown(
    cleaned_content: str,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """Split Markdown at headings/paragraphs, then split oversized blocks by sentences."""

    if max_chunk_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chunk_chars:
        raise ValueError("invalid chunking limits")

    heading_path: list[str] = []
    blocks: list[ChunkDraft] = []
    current_lines: list[str] = []
    current_path: tuple[str, ...] = ()

    def flush() -> None:
        if not current_lines:
            return
        body = "\n".join(current_lines).strip()
        if body:
            blocks.extend(
                _split_block(
                    body,
                    current_path,
                    max_chunk_chars=max_chunk_chars,
                    overlap_chars=overlap_chars,
                )
            )
        current_lines.clear()

    for line in cleaned_content.split("\n") + [""]:
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(heading.group(2).strip())
            current_path = tuple(heading_path)
            continue
        if not line.strip():
            flush()
            continue
        if not current_lines:
            current_path = tuple(heading_path)
        current_lines.append(line)
    return blocks


def _split_block(
    body: str,
    heading_path: tuple[str, ...],
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """Keep short blocks intact and split long prose without cutting every word."""

    prefix = f"{' / '.join(heading_path)}\n" if heading_path else ""
    available = max_chunk_chars - len(prefix)
    if len(body) <= available:
        return [ChunkDraft(prefix + body, heading_path)]

    sentences = [part.strip() for part in _SENTENCE_END.split(body) if part.strip()]
    if not sentences:
        sentences = [body]
    result: list[ChunkDraft] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > available:
            if current:
                result.append(ChunkDraft(prefix + current, heading_path))
                current = ""
            result.extend(
                _split_long_text(
                    sentence,
                    heading_path,
                    available=available,
                    overlap_chars=overlap_chars,
                    prefix=prefix,
                )
            )
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > available:
            result.append(ChunkDraft(prefix + current, heading_path))
            current = _overlap_tail(current, overlap_chars)
        current = f"{current} {sentence}".strip()
    if current:
        result.append(ChunkDraft(prefix + current, heading_path))
    return result


def _split_long_text(
    text: str,
    heading_path: tuple[str, ...],
    *,
    available: int,
    overlap_chars: int,
    prefix: str,
) -> list[ChunkDraft]:
    """Bound pathological paragraphs such as copied tables or long URLs."""

    result: list[ChunkDraft] = []
    start = 0
    while start < len(text):
        end = min(start + available, len(text))
        piece = text[start:end].strip()
        if piece:
            result.append(ChunkDraft(prefix + piece, heading_path))
        if end == len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return result


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """Use a bounded tail as context for the next chunk without exceeding limits."""

    if overlap_chars == 0:
        return ""
    return text[-overlap_chars:].lstrip()


def content_checksum(content: str) -> str:
    """Return a stable SHA-256 checksum for deduplication and audit records."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def versioned_document_id(source_uri: str, version: int) -> str:
    """Generate an opaque ID so source URLs never become database identifiers."""

    return hashlib.sha256(f"{source_uri}\n{version}".encode()).hexdigest()


def chunk_id(document_id: str, index: int, content: str) -> str:
    """Generate a deterministic ID for retry-safe chunk upserts."""

    return hashlib.sha256(
        f"{document_id}\n{index}\n{content_checksum(content)}".encode()
    ).hexdigest()
