"""新增通知偏好、安静时间和频率控制。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0022"
down_revision: str | None = "20260814_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为通知增加用户级授权策略，并保留抑制/延迟的可审计状态。"""

    op.add_column(
        "agent_notification_outbox",
        sa.Column(
            "suppressed_at", sa.DateTime(timezone=True), nullable=True, comment="通知被策略抑制时间"
        ),
    )
    op.add_column(
        "agent_notification_outbox",
        sa.Column(
            "suppression_reason",
            sa.Text(),
            nullable=True,
            comment="策略结果：USER_DISABLED 用户关闭、FREQUENCY_LIMIT 频率限制",
        ),
    )
    op.drop_constraint("ck_agent_notification_status", "agent_notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_agent_notification_status",
        "agent_notification_outbox",
        "status IN ('PENDING', 'PROCESSING', 'DEFERRED', 'PUBLISHED', 'RETRYABLE_FAILED', 'SUPPRESSED', 'DEAD')",
    )
    op.create_table(
        "agent_notification_preferences",
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="通知偏好所属用户"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="通知偏好所属机构"),
        sa.Column("notification_type", sa.Text(), nullable=False, comment="通知类型"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="用户授权开关：true 允许通知，false 抑制通知",
        ),
        sa.Column("quiet_start", sa.Time(), nullable=True, comment="安静时间起点（本地时间）"),
        sa.Column("quiet_end", sa.Time(), nullable=True, comment="安静时间终点（本地时间）"),
        sa.Column("timezone", sa.Text(), nullable=False, comment="安静时间使用的 IANA 时区"),
        sa.Column(
            "minimum_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="同类通知最小间隔；0 表示不限制",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="偏好首次创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="偏好最近修改时间",
        ),
        sa.PrimaryKeyConstraint(
            "subject_user_id",
            "organization_id",
            "notification_type",
            name="pk_agent_notification_preferences",
        ),
        sa.CheckConstraint(
            "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
            name="ck_agent_notification_preference_type",
        ),
        sa.CheckConstraint(
            "(quiet_start IS NULL AND quiet_end IS NULL) OR (quiet_start IS NOT NULL AND quiet_end IS NOT NULL)",
            name="ck_agent_notification_preference_quiet_pair",
        ),
        sa.CheckConstraint(
            "minimum_interval_seconds BETWEEN 0 AND 604800",
            name="ck_agent_notification_preference_interval",
        ),
        sa.CheckConstraint(
            "length(trim(timezone)) > 0", name="ck_agent_notification_preference_timezone"
        ),
    )
    op.create_table_comment(
        "agent_notification_preferences",
        "用户在机构和通知类型维度的授权、安静时间和频率策略；不保存通知正文",
    )


def downgrade() -> None:
    """删除通知偏好并恢复原有 Outbox 状态集合。"""

    op.drop_table("agent_notification_preferences")
    op.drop_constraint("ck_agent_notification_status", "agent_notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_agent_notification_status",
        "agent_notification_outbox",
        "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'RETRYABLE_FAILED', 'DEAD')",
    )
    op.drop_column("agent_notification_outbox", "suppression_reason")
    op.drop_column("agent_notification_outbox", "suppressed_at")
