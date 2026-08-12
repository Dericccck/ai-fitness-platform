"""Stable RAG domain objects independent of PostgreSQL or a vector vendor."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RetrievalScope:
    """Verified identity scope used to build server-side retrieval filters.

    The model or HTTP request never supplies these values. The API derives them
    from the signed AgentContext, and the repository turns them into SQL filters.
    """

    subject: str
    organization_ids: frozenset[str]
    roles: frozenset[str]


@dataclass(frozen=True)
class KnowledgeChunk:
    """A retrieved chunk with enough provenance for a user-visible citation."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    source_uri: str
    title: str
    document_type: str
    version: int
    similarity: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeDocumentInput:
    """A publishable document version that owns one or more chunks."""

    id: str
    organization_id: str | None
    title: str
    source_uri: str
    document_type: str
    visibility: str
    applicable_roles: tuple[str, ...]
    version: int
    status: str
    checksum: str
    effective_from: datetime
    effective_to: datetime | None


@dataclass(frozen=True)
class KnowledgeDocumentSnapshot:
    """Current persisted version used to decide whether re-indexing is needed."""

    id: str
    source_uri: str
    checksum: str
    version: int
    status: str


@dataclass(frozen=True)
class KnowledgeChunkInput:
    """Chunk input before persistence and embedding generation."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    content_hash: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    allowed_roles: tuple[str, ...]
    document_type: str
    effective_from: datetime
    effective_to: datetime | None
    metadata: dict[str, Any]
