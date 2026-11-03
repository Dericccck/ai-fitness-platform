"""完成凭据独立于响应保留期；摘要随完成事务发布。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0047"
down_revision: str | None = "20261006_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_chat_history_commits",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("thread_id", "owner_hash"),
        sa.CheckConstraint("version > 0", name="ck_chat_history_version"),
    )
    op.create_index("ix_chat_history_subject", "agent_chat_history_commits", ["subject_user_id"])
    op.create_index("ix_chat_history_cleanup", "agent_chat_history_commits", ["checked_at"])
    op.add_column("agent_session_summaries", sa.Column("source_run_id", sa.Text()))
    # 旧摘要未绑定来源，无法证明不包含取消轮次；以可信 checkpoint 重建。
    op.execute("DELETE FROM agent_session_summaries")


def downgrade() -> None:
    op.drop_column("agent_session_summaries", "source_run_id")
    op.drop_table("agent_chat_history_commits")
