"""增加通知渠道适配器的投递尝试记录。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0027"
down_revision: str | None = "20260815_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录每个 Outbox 事件在具体渠道上的开始、成功和失败结果。"""

    op.create_table(
        "agent_notification_delivery_attempts",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="投递尝试序号"
        ),
        sa.Column(
            "outbox_id",
            sa.Text(),
            sa.ForeignKey("agent_notification_outbox.id", ondelete="RESTRICT"),
            nullable=False,
            comment="关联的通知 Outbox 事件",
        ),
        sa.Column("channel", sa.Text(), nullable=False, comment="投递渠道，如 IN_APP"),
        sa.Column(
            "attempt_no", sa.Integer(), nullable=False, comment="该 Outbox 在该渠道的第几次尝试"
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="状态：STARTED 开始、SUCCEEDED 成功、RETRYABLE_FAILED 可重试失败、FINAL_FAILED 最终失败",
        ),
        sa.Column("error_code", sa.Text(), nullable=True, comment="受控失败编码，不保存异常正文"),
        sa.Column(
            "provider_message_id", sa.Text(), nullable=True, comment="渠道或供应商返回的消息 ID"
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="开始时间",
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        sa.CheckConstraint("channel IN ('IN_APP')", name="ck_agent_notification_attempt_channel"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_agent_notification_attempt_no"),
        sa.CheckConstraint(
            "status IN ('STARTED', 'SUCCEEDED', 'RETRYABLE_FAILED', 'FINAL_FAILED')",
            name="ck_agent_notification_attempt_status",
        ),
        sa.UniqueConstraint(
            "outbox_id", "channel", "attempt_no", name="uq_agent_notification_attempt_identity"
        ),
    )
    op.create_index(
        "ix_agent_notification_attempt_outbox_created",
        "agent_notification_delivery_attempts",
        ["outbox_id", "channel", "started_at", "id"],
    )
    op.create_table_comment(
        "agent_notification_delivery_attempts",
        "通知渠道投递尝试和受控结果；不保存通知正文",
    )


def downgrade() -> None:
    """删除通知渠道投递尝试记录。"""

    op.drop_index(
        "ix_agent_notification_attempt_outbox_created",
        table_name="agent_notification_delivery_attempts",
    )
    op.drop_table("agent_notification_delivery_attempts")
