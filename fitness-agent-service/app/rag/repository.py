"""PostgreSQL repository for permission-aware vector retrieval."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text

from app.infrastructure.database import Database

from .models import (
    KnowledgeChunk,
    KnowledgeChunkInput,
    KnowledgeDocumentInput,
    KnowledgeDocumentSnapshot,
    RetrievalScope,
)


class KnowledgeRepository:
    """Persist and search Agent knowledge without exposing a raw DB to Agents.

    PostgreSQL remains the source of truth for indexed knowledge metadata. The
    vector column is only a retrieval accelerator; every search applies tenant,
    role, ownership, publication, and effective-time constraints in SQL before
    a candidate is sent to the Reranker or the LLM.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_current_document(self, source_uri: str) -> KnowledgeDocumentSnapshot | None:
        """Return the currently published version for an immutable source URI."""

        statement = text(
            """
            SELECT id, source_uri, checksum, version, status
            FROM knowledge_documents
            WHERE source_uri = :source_uri AND status = 'PUBLISHED'
            ORDER BY version DESC
            LIMIT 1
            """
        )
        async with self._database.engine.connect() as connection:
            row = (
                (await connection.execute(statement, {"source_uri": source_uri})).mappings().first()
            )
        if row is None:
            return None
        return KnowledgeDocumentSnapshot(
            id=str(row["id"]),
            source_uri=str(row["source_uri"]),
            checksum=str(row["checksum"]),
            version=int(row["version"]),
            status=str(row["status"]),
        )

    async def insert_chunks(
        self,
        chunks: Sequence[KnowledgeChunkInput],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Insert embedded chunks in one transaction.

        The caller is responsible for publishing the document version before
        exposing it to retrieval. ``executemany`` keeps one document batch
        atomic, so a partial embedding write cannot create a half-indexed
        document.
        """

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        if not chunks:
            return

        statement = text(
            """
            INSERT INTO knowledge_chunks (
                id, document_id, chunk_index, content, content_hash, embedding,
                organization_id, owner_user_id, visibility, allowed_roles,
                document_type, effective_from, effective_to, metadata
            ) VALUES (
                :id, :document_id, :chunk_index, :content, :content_hash,
                CAST(:embedding AS vector), :organization_id, :owner_user_id,
                :visibility, :allowed_roles, :document_type, :effective_from,
                :effective_to, CAST(:metadata AS json)
            )
            """
        )
        params = _chunk_params(chunks, embeddings)
        async with self._database.engine.begin() as connection:
            await connection.execute(statement, params)

    async def replace_document(
        self,
        document: KnowledgeDocumentInput,
        chunks: Sequence[KnowledgeChunkInput],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Upsert one document version and replace its chunks atomically.

        Re-indexing is intentionally a single database transaction. Until the
        transaction commits, the published document still exposes its previous
        chunks; after commit, no half-old/half-new version can be retrieved.
        """

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        if not chunks:
            raise ValueError("a knowledge document must contain at least one chunk")

        document_statement = text(
            """
            INSERT INTO knowledge_documents (
                id, organization_id, title, source_uri, document_type, visibility,
                applicable_roles, version, status, checksum, effective_from, effective_to
            ) VALUES (
                :id, :organization_id, :title, :source_uri, :document_type,
                :visibility, :applicable_roles, :version, :status, :checksum,
                :effective_from, :effective_to
            )
            ON CONFLICT (id) DO UPDATE SET
                organization_id = EXCLUDED.organization_id,
                title = EXCLUDED.title,
                source_uri = EXCLUDED.source_uri,
                document_type = EXCLUDED.document_type,
                visibility = EXCLUDED.visibility,
                applicable_roles = EXCLUDED.applicable_roles,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                checksum = EXCLUDED.checksum,
                effective_from = EXCLUDED.effective_from,
                effective_to = EXCLUDED.effective_to,
                updated_at = CURRENT_TIMESTAMP
            """
        )
        delete_statement = text("DELETE FROM knowledge_chunks WHERE document_id = :document_id")
        archive_statement = text(
            """
            UPDATE knowledge_documents
            SET status = 'ARCHIVED', updated_at = CURRENT_TIMESTAMP
            WHERE source_uri = :source_uri
              AND id <> :document_id
              AND status = 'PUBLISHED'
            """
        )
        chunk_statement = text(
            """
            INSERT INTO knowledge_chunks (
                id, document_id, chunk_index, content, content_hash, embedding,
                organization_id, owner_user_id, visibility, allowed_roles,
                document_type, effective_from, effective_to, metadata
            ) VALUES (
                :id, :document_id, :chunk_index, :content, :content_hash,
                CAST(:embedding AS vector), :organization_id, :owner_user_id,
                :visibility, :allowed_roles, :document_type, :effective_from,
                :effective_to, CAST(:metadata AS json)
            )
            """
        )
        document_params = {
            "id": document.id,
            "organization_id": document.organization_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "document_type": document.document_type,
            "visibility": document.visibility,
            "applicable_roles": list(document.applicable_roles),
            "version": document.version,
            "status": document.status,
            "checksum": document.checksum,
            "effective_from": document.effective_from,
            "effective_to": document.effective_to,
        }
        async with self._database.engine.begin() as connection:
            # Only one version of a source is searchable at a time. This is
            # part of the same transaction as the new version, so a failed
            # embedding write cannot hide the previously published version.
            await connection.execute(
                archive_statement,
                {"source_uri": document.source_uri, "document_id": document.id},
            )
            await connection.execute(document_statement, document_params)
            await connection.execute(delete_statement, {"document_id": document.id})
            await connection.execute(chunk_statement, _chunk_params(chunks, embeddings))

    async def search_candidates(
        self,
        embedding: Sequence[float],
        scope: RetrievalScope,
        *,
        limit: int,
    ) -> list[KnowledgeChunk]:
        """Recall authorized candidates using pgvector cosine distance."""

        if not scope.organization_ids or not scope.roles:
            return []

        role_clauses = [
            f"allowed_roles && ARRAY[:role_{index}]::text[]"
            for index, _ in enumerate(sorted(scope.roles))
        ]
        role_parameters = {f"role_{index}": role for index, role in enumerate(sorted(scope.roles))}
        statement = text(
            """
            SELECT
                c.id,
                c.document_id,
                c.chunk_index,
                c.content,
                d.source_uri,
                d.title,
                c.document_type,
                d.version,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity,
                c.metadata
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE d.status = 'PUBLISHED'
              AND c.visibility = d.visibility
              AND c.effective_from <= CURRENT_TIMESTAMP
              AND (c.effective_to IS NULL OR c.effective_to > CURRENT_TIMESTAMP)
              AND (
                    c.visibility = 'GLOBAL'
                    OR (c.visibility = 'ORGANIZATION' AND c.organization_id IN :organization_ids)
                    OR (c.visibility = 'PRIVATE' AND c.owner_user_id = :subject)
              )
              AND (
                    cardinality(c.allowed_roles) = 0
                    OR :role_filter_enabled = false
                    OR ({role_filter})
              )
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """.format(role_filter=" OR ".join(role_clauses))
        ).bindparams(bindparam("organization_ids", expanding=True))
        params: dict[str, Any] = {
            "embedding": _vector_literal(embedding),
            "organization_ids": sorted(scope.organization_ids),
            "subject": scope.subject,
            "role_filter_enabled": bool(role_clauses),
            "limit": limit,
            **role_parameters,
        }
        async with self._database.engine.connect() as connection:
            result = await connection.execute(statement, params)
            rows = result.mappings().all()
        return [
            KnowledgeChunk(
                id=str(row["id"]),
                document_id=str(row["document_id"]),
                chunk_index=int(row["chunk_index"]),
                content=str(row["content"]),
                source_uri=str(row["source_uri"]),
                title=str(row["title"]),
                document_type=str(row["document_type"]),
                version=int(row["version"]),
                similarity=float(row["similarity"]),
                metadata=dict(row["metadata"] or {}),
            )
            for row in rows
        ]


def _vector_literal(values: Sequence[float]) -> str:
    """Serialize a vector for an explicit PostgreSQL cast.

    The explicit cast keeps the repository independent of SQLAlchemy ORM model
    state and makes the query type visible. Values are validated as finite
    numbers so malformed model output cannot reach the database driver.
    """

    if not values:
        raise ValueError("embedding must not be empty")
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _chunk_params(
    chunks: Sequence[KnowledgeChunkInput],
    embeddings: Sequence[Sequence[float]],
) -> list[dict[str, Any]]:
    """Build one consistent parameter shape for insert and replace operations."""

    return [
        {
            "id": chunk.id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "embedding": _vector_literal(embedding),
            "organization_id": chunk.organization_id,
            "owner_user_id": chunk.owner_user_id,
            "visibility": chunk.visibility,
            "allowed_roles": list(chunk.allowed_roles),
            "document_type": chunk.document_type,
            "effective_from": chunk.effective_from,
            "effective_to": chunk.effective_to,
            "metadata": json.dumps(chunk.metadata, ensure_ascii=False),
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
