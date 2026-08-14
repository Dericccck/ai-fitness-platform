"""新增 Agent 站内通知收件箱。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0021"
down_revision: str | None = "20260814_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建站内通知收件箱，作为当前无需外部供应商的通知渠道。"""

    op.create_table(
        "agent_in_app_notifications",
        sa.Column("id", sa.Text(), primary_key=True, comment="站内通知唯一标识"),
        sa.Column("notification_type", sa.Text(), nullable=False, comment="通知类型"),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="通知接收用户"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="通知所属机构"),
        sa.Column("aggregate_type", sa.Text(), nullable=False, comment="业务聚合类型"),
        sa.Column("aggregate_id", sa.Text(), nullable=False, comment="业务聚合 ID"),
        sa.Column("dedupe_key", sa.Text(), nullable=False, comment="通知去重键"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="UNREAD",
            comment="状态：UNREAD 未读、READ 已读、DISMISSED 已忽略",
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True, comment="首次已读时间"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="通知生成时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="最近状态变更时间",
        ),
        sa.CheckConstraint(
            "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
            name="ck_agent_in_app_notification_type",
        ),
        sa.CheckConstraint(
            "status IN ('UNREAD', 'READ', 'DISMISSED')",
            name="ck_agent_in_app_notification_status",
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_agent_in_app_notification_dedupe"),
    )
    op.create_index(
        "ix_agent_in_app_notification_subject_status_created",
        "agent_in_app_notifications",
        ["subject_user_id", "organization_id", "status", "created_at", "id"],
    )
    op.create_table_comment(
        "agent_in_app_notifications",
        "Agent 站内通知收件箱；只保存通知路由和业务聚合 ID，不保存候选正文",
    )


def downgrade() -> None:
    """删除站内通知收件箱。"""

    op.drop_index(
        "ix_agent_in_app_notification_subject_status_created",
        table_name="agent_in_app_notifications",
    )
    op.drop_table("agent_in_app_notifications")
