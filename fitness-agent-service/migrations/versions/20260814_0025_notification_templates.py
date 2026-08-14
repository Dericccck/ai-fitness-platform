"""增加版本化通知模板和站内通知渲染快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0025"
down_revision: str | None = "20260814_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立模板审核发布链路，并给历史站内通知补充稳定展示内容。"""

    op.create_table(
        "agent_notification_templates",
        sa.Column(
            "template_key", sa.Text(), nullable=False, comment="通知模板键，通常对应通知类型"
        ),
        sa.Column("channel", sa.Text(), nullable=False, comment="投递渠道；当前仅支持 IN_APP"),
        sa.Column("version", sa.Integer(), nullable=False, comment="模板递增版本"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="状态：DRAFT 草稿、APPROVED 已审核、PUBLISHED 已发布、RETIRED 已退役",
        ),
        sa.Column("title_template", sa.Text(), nullable=False, comment="通知标题模板"),
        sa.Column("body_template", sa.Text(), nullable=False, comment="通知正文模板"),
        sa.Column(
            "variables",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="允许使用的变量名数组",
        ),
        sa.Column("created_by", sa.Text(), nullable=False, comment="模板创建人或系统标识"),
        sa.Column("approved_by", sa.Text(), nullable=True, comment="模板审核人"),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), nullable=True, comment="模板发布时间"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="模板创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="模板最近修改时间",
        ),
        sa.PrimaryKeyConstraint(
            "template_key", "channel", "version", name="pk_agent_notification_templates"
        ),
        sa.CheckConstraint("channel IN ('IN_APP')", name="ck_agent_notification_template_channel"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'PUBLISHED', 'RETIRED')",
            name="ck_agent_notification_template_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_notification_template_version"),
        sa.CheckConstraint(
            "length(trim(template_key)) BETWEEN 1 AND 128",
            name="ck_agent_notification_template_key",
        ),
        sa.CheckConstraint(
            "length(trim(title_template)) BETWEEN 1 AND 200",
            name="ck_agent_notification_template_title",
        ),
        sa.CheckConstraint(
            "length(trim(body_template)) BETWEEN 1 AND 2000",
            name="ck_agent_notification_template_body",
        ),
    )
    op.create_index(
        "uq_agent_notification_template_published",
        "agent_notification_templates",
        ["template_key", "channel"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_table_comment(
        "agent_notification_templates",
        "版本化通知模板控制面；只有已审核并发布的模板可被 Worker 渲染",
    )
    op.execute(
        """
        INSERT INTO agent_notification_templates (
            template_key, channel, version, status, title_template, body_template,
            variables, created_by, approved_by, published_at
        ) VALUES (
            'MEMORY_CANDIDATE_PENDING', 'IN_APP', 1, 'PUBLISHED',
            '有一条待确认的记忆候选',
            '请打开健身助手，审核这条待确认的个人偏好记忆。',
            '[]'::jsonb, 'SYSTEM', 'SYSTEM', CURRENT_TIMESTAMP
        )
        """
    )
    op.add_column(
        "agent_in_app_notifications",
        sa.Column(
            "template_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="渲染该通知时使用的模板版本",
        ),
    )
    op.add_column(
        "agent_in_app_notifications",
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            server_default="健身助手通知",
            comment="通知标题渲染快照",
        ),
    )
    op.add_column(
        "agent_in_app_notifications",
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            server_default="请打开健身助手查看待处理事项。",
            comment="通知正文渲染快照",
        ),
    )
    op.create_check_constraint(
        "ck_agent_in_app_notification_template_version",
        "agent_in_app_notifications",
        "template_version >= 1",
    )


def downgrade() -> None:
    """移除通知模板和展示快照字段。"""

    op.drop_constraint(
        "ck_agent_in_app_notification_template_version", "agent_in_app_notifications", type_="check"
    )
    op.drop_column("agent_in_app_notifications", "body")
    op.drop_column("agent_in_app_notifications", "title")
    op.drop_column("agent_in_app_notifications", "template_version")
    op.drop_index(
        "uq_agent_notification_template_published", table_name="agent_notification_templates"
    )
    op.drop_table("agent_notification_templates")
