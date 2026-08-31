"""增加用于权限过滤混合召回的 PostgreSQL 全文和三元组索引。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """构建词法索引，不替换现有向量索引。"""

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
    """删除词法索引，同时保留 pgvector 和知识内容。"""

    op.execute("DROP INDEX IF EXISTS knowledge_chunks_content_trgm_idx")
    op.execute("DROP INDEX IF EXISTS knowledge_chunks_search_vector_gin_idx")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN search_vector")
