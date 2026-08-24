"""增加主动提醒事件 Inbox，并扩展健身业务通知类型。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0034"
down_revision: str | None = "20260824_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTIFICATION_TYPES = (
    "'MEMORY_CANDIDATE_PENDING'",
    "'APPOINTMENT_CREATED'",
    "'APPOINTMENT_RESCHEDULED'",
    "'APPOINTMENT_CANCELLED'",
    "'TRAINING_PLAN_PUBLISHED'",
    "'TRAINING_PLAN_REVIEW_REQUIRED'",
)


def upgrade() -> None:
    """保存跨服务事件的消费状态，确保 RabbitMQ 重投不会重复生成站内通知。"""

    notification_types = ", ".join(_NOTIFICATION_TYPES)
    op.drop_constraint("ck_agent_notification_type", "agent_notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_agent_notification_type",
        "agent_notification_outbox",
        f"notification_type IN ({notification_types})",
    )
    op.drop_constraint(
        "ck_agent_notification_preference_type",
        "agent_notification_preferences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_notification_preference_type",
        "agent_notification_preferences",
        f"notification_type IN ({notification_types})",
    )

    op.create_table(
        "agent_proactive_event_inbox",
        sa.Column("event_id", sa.Text(), nullable=False, comment="来源业务事件唯一标识"),
        sa.Column("source", sa.Text(), nullable=False, comment="事件来源服务，如 booking"),
        sa.Column("event_type", sa.Text(), nullable=False, comment="事件类型"),
        sa.Column("aggregate_id", sa.Text(), nullable=False, comment="业务聚合 ID"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="事件所属机构"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="经过契约校验的路由字段，不保存通知正文",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PENDING",
            comment="状态：PENDING 待处理、PROCESSING 处理中、PROCESSED 已处理、RETRYABLE_FAILED 可重试失败、DEAD 死信",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="处理尝试次数",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="允许下次处理的时间",
        ),
        sa.Column("locked_by", sa.Text(), nullable=True, comment="当前处理 Worker 标识"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True, comment="领取时间"),
        sa.Column("last_error_code", sa.Text(), nullable=True, comment="最近一次受控错误码"),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=True, comment="处理完成时间"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="事件进入 Inbox 的时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="最近状态更新时间",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_agent_proactive_event_inbox"),
        sa.CheckConstraint(
            "event_type IN ('APPOINTMENT_CREATED', 'APPOINTMENT_RESCHEDULED', 'APPOINTMENT_CANCELLED', 'TRAINING_PLAN_PUBLISHED', 'TRAINING_PLAN_REVIEW_REQUIRED')",
            name="ck_agent_proactive_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'RETRYABLE_FAILED', 'DEAD')",
            name="ck_agent_proactive_event_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_agent_proactive_event_attempts"),
    )
    op.create_index(
        "ix_agent_proactive_event_claimable",
        "agent_proactive_event_inbox",
        ["status", "available_at", "created_at", "event_id"],
    )
    op.create_table_comment(
        "agent_proactive_event_inbox",
        "主动提醒事件 Inbox；先幂等落库，再生成通知 Outbox，避免消息重投造成重复通知",
    )

    templates = [
        (
            "APPOINTMENT_CREATED",
            "预约已创建",
            "有新的预约安排，请打开健身助手查看课程详情。",
        ),
        (
            "APPOINTMENT_RESCHEDULED",
            "预约时间已变更",
            "你的预约时间发生变更，请打开健身助手查看最新安排。",
        ),
        (
            "APPOINTMENT_CANCELLED",
            "预约已取消",
            "你的预约已取消，请打开健身助手查看详情。",
        ),
        (
            "TRAINING_PLAN_PUBLISHED",
            "训练计划已发布",
            "你的训练计划已发布，请打开健身助手查看训练安排。",
        ),
        (
            "TRAINING_PLAN_REVIEW_REQUIRED",
            "有训练计划待审核",
            "有一份训练计划等待教练审核，请打开健身助手处理。",
        ),
    ]
    for key, title, body in templates:
        op.execute(
            sa.text(
                """
                INSERT INTO agent_notification_templates (
                    template_key, channel, version, status, title_template, body_template,
                    variables, created_by, approved_by, published_at
                ) VALUES (:key, 'IN_APP', 1, 'PUBLISHED', :title, :body, '[]'::jsonb,
                          'SYSTEM', 'SYSTEM', CURRENT_TIMESTAMP)
                ON CONFLICT (template_key, channel, version) DO NOTHING
                """
            ).bindparams(key=key, title=title, body=body)
        )


def downgrade() -> None:
    """删除主动提醒事件 Inbox，并恢复 Memory-only 通知类型约束。"""

    op.execute(
        sa.text(
            "DELETE FROM agent_notification_templates WHERE template_key IN "
            "('APPOINTMENT_CREATED', 'APPOINTMENT_RESCHEDULED', 'APPOINTMENT_CANCELLED', "
            "'TRAINING_PLAN_PUBLISHED', 'TRAINING_PLAN_REVIEW_REQUIRED')"
        )
    )
    op.drop_table("agent_proactive_event_inbox")
    op.drop_constraint(
        "ck_agent_notification_preference_type", "agent_notification_preferences", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_notification_preference_type",
        "agent_notification_preferences",
        "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
    )
    op.drop_constraint("ck_agent_notification_type", "agent_notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_agent_notification_type",
        "agent_notification_outbox",
        "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
    )
