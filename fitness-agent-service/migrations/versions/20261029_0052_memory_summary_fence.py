"""序列化记忆变更与派生摘要发布。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0052"
down_revision: str | None = "20261029_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_subject_fences",
        sa.Column("subject_user_id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # 旧摘要没有依赖版本，不能证明未包含撤销过的记忆；仅丢弃可重建的派生摘要。
    op.execute("DELETE FROM agent_session_summaries")


def downgrade() -> None:
    op.drop_table("agent_memory_subject_fences")
