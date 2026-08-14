"""新增 Agent 通知事务性 Outbox。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0020"
down_revision: str | None = "20260814_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存待发布通知事件，正文只通过候选 ID 延迟读取。"""

    op.create_table(
        "agent_notification_outbox",
        sa.Column("id", sa.Text(), primary_key=True, comment="通知事件唯一标识"),
        sa.Column("notification_type", sa.Text(), nullable=False, comment="通知类型"),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="通知接收用户"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="通知所属机构"),
        sa.Column("aggregate_type", sa.Text(), nullable=False, comment="业务聚合类型"),
        sa.Column("aggregate_id", sa.Text(), nullable=False, comment="业务聚合 ID"),
        sa.Column("dedupe_key", sa.Text(), nullable=False, comment="通知幂等键"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="下游路由参数；禁止写入候选正文等敏感数据",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PENDING",
            comment="状态：PENDING 待发布、PROCESSING 处理中、PUBLISHED 已发布、RETRYABLE_FAILED 可重试失败、DEAD 死信",
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0", comment="已尝试次数"
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="允许下次领取的时间",
        ),
        sa.Column("locked_by", sa.Text(), nullable=True, comment="当前领取者实例标识"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True, comment="领取时间"),
        sa.Column("last_error_code", sa.Text(), nullable=True, comment="最近一次失败码"),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), nullable=True, comment="发布成功时间"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="更新时间",
        ),
        sa.CheckConstraint(
            "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
            name="ck_agent_notification_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'RETRYABLE_FAILED', 'DEAD')",
            name="ck_agent_notification_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_agent_notification_attempts"),
        sa.UniqueConstraint("dedupe_key", name="uq_agent_notification_dedupe"),
    )
    op.create_index(
        "ix_agent_notification_claimable",
        "agent_notification_outbox",
        ["status", "available_at", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_notification_subject_created",
        "agent_notification_outbox",
        ["subject_user_id", "organization_id", "created_at"],
    )
    op.create_table_comment(
        "agent_notification_outbox",
        "Agent 通知事务性 Outbox；只保存路由参数和候选 ID，不保存通知正文或敏感用户内容",
    )


def downgrade() -> None:
    """删除通知 Outbox。"""

    op.drop_index("ix_agent_notification_subject_created", table_name="agent_notification_outbox")
    op.drop_index("ix_agent_notification_claimable", table_name="agent_notification_outbox")
    op.drop_table("agent_notification_outbox")
