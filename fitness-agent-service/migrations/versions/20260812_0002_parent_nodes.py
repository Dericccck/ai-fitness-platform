"""Add parent records for small retrieval chunks and richer source context."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0002"
down_revision: str | None = "20260812_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create non-vector parent nodes and link child chunks to them."""

    op.create_table(
        "knowledge_parents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section_path", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("table_index", sa.Integer(), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=True),
        sa.Column("row_end", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "id", name="uq_knowledge_parents_document_id"),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "parent_id",
            sa.Text(),
            sa.ForeignKey("knowledge_parents.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_knowledge_parents_document", "knowledge_parents", ["document_id"])
    op.create_index("ix_knowledge_chunks_parent", "knowledge_chunks", ["parent_id"])


def downgrade() -> None:
    """Remove parent links and parent nodes."""

    op.drop_index("ix_knowledge_chunks_parent", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_parents_document", table_name="knowledge_parents")
    op.drop_column("knowledge_chunks", "parent_id")
    op.drop_table("knowledge_parents")
