"""Create the versioned fitness knowledge base and pgvector index.

The tables in this migration belong to the Agent service. They are deliberately
not the source of truth for contracts, bookings, training records, or other
business facts managed by the Java application.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260812_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create documents, chunks, permission metadata, and ANN indexes."""

    # pgvector is an infrastructure dependency, so migrations fail explicitly
    # when the deployment image does not contain the extension instead of
    # silently creating a table that cannot execute vector search.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("applicable_roles", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "visibility IN ('GLOBAL', 'ORGANIZATION', 'PRIVATE')",
            name="ck_knowledge_documents_visibility",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_knowledge_documents_status",
        ),
        sa.UniqueConstraint("source_uri", "version", name="uq_knowledge_documents_source_version"),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        # HNSW requires a fixed vector dimension. 1536 is the project contract
        # for the first production Embedding model; changing it requires a
        # versioned migration and a full re-index, not an environment-only edit.
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("allowed_roles", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "visibility IN ('GLOBAL', 'ORGANIZATION', 'PRIVATE')",
            name="ck_knowledge_chunks_visibility",
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunks_document_index"
        ),
    )

    # The vector index accelerates candidate recall. Authorization is still
    # applied in the WHERE clause before content is returned to the reranker.
    op.execute(
        "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index(
        "ix_knowledge_chunks_scope",
        "knowledge_chunks",
        ["organization_id", "visibility", "document_type", "effective_from"],
    )
    op.create_index(
        "ix_knowledge_chunks_document",
        "knowledge_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )


def downgrade() -> None:
    """Remove the Agent knowledge base while retaining the shared extension."""

    op.drop_index("ix_knowledge_chunks_document", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_scope", table_name="knowledge_chunks")
    op.execute("DROP INDEX IF EXISTS knowledge_chunks_embedding_hnsw_idx")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
