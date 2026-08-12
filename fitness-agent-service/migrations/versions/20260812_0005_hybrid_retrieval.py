"""Add PostgreSQL full-text and trigram indexes for permission-filtered hybrid recall."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Build lexical indexes without replacing the existing vector index."""

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "ALTER TABLE knowledge_chunks ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED"
    )
    op.execute(
        "CREATE INDEX knowledge_chunks_search_vector_gin_idx "
        "ON knowledge_chunks USING gin (search_vector)"
    )
    op.execute(
        "CREATE INDEX knowledge_chunks_content_trgm_idx "
        "ON knowledge_chunks USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    """Remove lexical indexes while retaining pgvector and knowledge content."""

    op.execute("DROP INDEX IF EXISTS knowledge_chunks_content_trgm_idx")
    op.execute("DROP INDEX IF EXISTS knowledge_chunks_search_vector_gin_idx")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN search_vector")
