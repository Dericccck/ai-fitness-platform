"""增加通知模板生命周期不可变审计事件。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0026"
down_revision: str | None = "20260814_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录模板创建、审核和发布，不复制标题、正文或变量。"""

    op.create_table(
        "agent_notification_template_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="事件序号"),
        sa.Column("template_key", sa.Text(), nullable=False, comment="通知模板键快照"),
        sa.Column("channel", sa.Text(), nullable=False, comment="通知渠道快照"),
        sa.Column("version", sa.Integer(), nullable=False, comment="模板版本快照"),
        sa.Column(
            "event_type",
            sa.Text(),
            nullable=False,
            comment="事件类型：DRAFT_CREATED 草稿、APPROVED 审核、PUBLISHED 发布",
        ),
        sa.Column("actor_user_id", sa.Text(), nullable=False, comment="操作者主体快照"),
        sa.Column("status_after", sa.Text(), nullable=False, comment="事件后的模板状态"),
        sa.Column("operation_id", sa.Text(), nullable=False, comment="写操作幂等键"),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="非正文审计元数据，如发布时退役的旧版本列表",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="事件发生时间",
        ),
        sa.CheckConstraint(
            "channel IN ('IN_APP')", name="ck_agent_notification_template_event_channel"
        ),
        sa.CheckConstraint(
            "event_type IN ('DRAFT_CREATED', 'APPROVED', 'PUBLISHED')",
            name="ck_agent_notification_template_event_type",
        ),
        sa.CheckConstraint(
            "status_after IN ('DRAFT', 'APPROVED', 'PUBLISHED')",
            name="ck_agent_notification_template_event_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_notification_template_event_version"),
        sa.CheckConstraint(
            "length(trim(operation_id)) BETWEEN 1 AND 128",
            name="ck_agent_notification_template_event_operation",
        ),
        sa.UniqueConstraint("operation_id", name="uq_agent_notification_template_event_operation"),
    )
    op.create_index(
        "ix_agent_notification_template_events_template_created",
        "agent_notification_template_events",
        ["template_key", "channel", "version", "created_at", "id"],
    )
    op.create_table_comment(
        "agent_notification_template_events",
        "通知模板创建、审核和发布的不可变审计事件；不保存模板正文",
    )


def downgrade() -> None:
    """删除通知模板生命周期审计事件。"""

    op.drop_index(
        "ix_agent_notification_template_events_template_created",
        table_name="agent_notification_template_events",
    )
    op.drop_table("agent_notification_template_events")
