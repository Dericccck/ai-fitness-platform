"""将知识库向量维度切换到本地 BGE-M3 的 1024 维。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260812_0009"
down_revision: str | None = "20260812_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在没有旧向量时安全切换维度，避免静默截断已发布 Embedding。"""

    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT COUNT(*) FROM knowledge_chunks")).scalar_one()
    if int(count) != 0:
        raise RuntimeError(
            "knowledge_chunks already contains vectors; create a dual-write migration and "
            "complete a controlled re-index before switching from 1536 to 1024 dimensions"
        )
    op.alter_column(
        "knowledge_chunks",
        "embedding",
        existing_type=Vector(1536),
        type_=Vector(1024),
        existing_nullable=False,
    )


def downgrade() -> None:
    """回滚前同样要求没有 1024 维向量，防止破坏可检索数据。"""

    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT COUNT(*) FROM knowledge_chunks")).scalar_one()
    if int(count) != 0:
        raise RuntimeError("knowledge_chunks contains 1024-dimensional vectors; re-index first")
    op.alter_column(
        "knowledge_chunks",
        "embedding",
        existing_type=Vector(1024),
        type_=Vector(1536),
        existing_nullable=False,
    )
