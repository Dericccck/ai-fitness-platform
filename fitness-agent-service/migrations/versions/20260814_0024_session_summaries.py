"""新增短期会话摘要表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0024"
down_revision: str | None = "20260814_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存加密的当前会话摘要，按主体隔离并由 Worker 到期删除。"""

    op.create_table(
        "agent_session_summaries",
        sa.Column(
            "thread_id", sa.Text(), primary_key=True, comment="脱敏后的 LangGraph 会话线程标识"
        ),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="签名主体用户标识"),
        sa.Column(
            "summary_ciphertext",
            sa.LargeBinary(),
            nullable=False,
            comment="AES-GCM 加密的短期摘要正文，禁止保存明文",
        ),
        sa.Column("summary_key_version", sa.Text(), nullable=False, comment="摘要加密密钥版本"),
        sa.Column(
            "summary_hash", sa.String(length=64), nullable=False, comment="摘要明文 SHA-256 校验值"
        ),
        sa.Column(
            "summary_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="摘要替换版本",
        ),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="生成该摘要时已纳入的会话消息数量",
        ),
        sa.Column(
            "retention_until",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="短期摘要允许保留到的时间，默认 7 天",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="首次生成时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="最近替换时间",
        ),
        sa.CheckConstraint(
            "length(thread_id) BETWEEN 1 AND 200", name="ck_agent_session_summary_thread"
        ),
        sa.CheckConstraint(
            "length(subject_user_id) BETWEEN 1 AND 200", name="ck_agent_session_summary_subject"
        ),
        sa.CheckConstraint(
            "length(summary_key_version) BETWEEN 1 AND 100",
            name="ck_agent_session_summary_key_version",
        ),
        sa.CheckConstraint("summary_hash ~ '^[0-9a-f]{64}$'", name="ck_agent_session_summary_hash"),
        sa.CheckConstraint("summary_version >= 1", name="ck_agent_session_summary_version"),
        sa.CheckConstraint("message_count >= 0", name="ck_agent_session_summary_message_count"),
    )
    op.create_index(
        "ix_agent_session_summaries_retention_due",
        "agent_session_summaries",
        ["retention_until", "thread_id"],
    )
    op.create_index(
        "ix_agent_session_summaries_subject_updated",
        "agent_session_summaries",
        ["subject_user_id", "updated_at"],
    )
    op.create_table_comment(
        "agent_session_summaries",
        "当前会话的加密短期上下文摘要；不属于长期 Memory、业务事实或权限数据，按期限删除",
    )


def downgrade() -> None:
    """删除短期会话摘要表。"""

    op.drop_index(
        "ix_agent_session_summaries_subject_updated", table_name="agent_session_summaries"
    )
    op.drop_index("ix_agent_session_summaries_retention_due", table_name="agent_session_summaries")
    op.drop_table("agent_session_summaries")
