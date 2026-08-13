"""新增 Agent 写操作确认单和不可变确认事件。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0013"
down_revision: str | None = "20260813_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存写操作授权事实，但不保存 Confirmation Token。"""

    op.create_table(
        "agent_action_confirmations",
        sa.Column("id", sa.Text(), primary_key=True, comment="确认单唯一标识"),
        sa.Column(
            "protocol_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="确认协议版本",
        ),
        sa.Column("thread_id", sa.Text(), nullable=False, comment="脱敏后的 LangGraph 会话标识"),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="发起确认的用户主体标识"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="本次动作所属机构"),
        sa.Column("tool_id", sa.Text(), nullable=False, comment="版本化 Agent 工具标识"),
        sa.Column("risk_level", sa.Text(), nullable=False, comment="动作风险级别"),
        sa.Column("action", sa.Text(), nullable=False, comment="业务动作名称"),
        sa.Column("resource_type", sa.Text(), nullable=False, comment="资源类型"),
        sa.Column("resource_id", sa.Text(), nullable=True, comment="资源标识"),
        sa.Column("expected_resource_version", sa.Integer(), nullable=True, comment="预期资源版本"),
        sa.Column("request_id", sa.Text(), nullable=False, comment="业务请求幂等标识"),
        sa.Column("payload_hash", sa.Text(), nullable=False, comment="规范化执行参数 SHA-256"),
        sa.Column(
            "display_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="前端确认卡片使用的确定性脱敏摘要",
        ),
        sa.Column(
            "payload_ciphertext",
            sa.LargeBinary(),
            nullable=False,
            comment="加密后的精确执行参数，不允许保存明文",
        ),
        sa.Column("payload_key_version", sa.Text(), nullable=False, comment="执行参数加密密钥版本"),
        sa.Column(
            "authorization_status",
            sa.Text(),
            nullable=False,
            server_default="PENDING",
            comment="授权状态",
        ),
        sa.Column(
            "execution_status",
            sa.Text(),
            nullable=False,
            server_default="NOT_STARTED",
            comment="执行状态",
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="0", comment="乐观锁版本"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="创建时间",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="授权过期时间"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True, comment="批准时间"),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True, comment="拒绝时间"),
        sa.Column(
            "execution_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="执行领取时间",
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="执行完成时间"),
        sa.Column("decision_request_id", sa.Text(), nullable=True, comment="确认决定幂等标识"),
        sa.Column("credential_jti", sa.Text(), nullable=True, comment="一次性确认凭证标识"),
        sa.Column(
            "credential_consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="凭证消费时间",
        ),
        sa.Column("last_error_code", sa.Text(), nullable=True, comment="最近一次稳定错误码"),
        sa.CheckConstraint("protocol_version > 0", name="ck_agent_confirmation_protocol_positive"),
        sa.CheckConstraint("version >= 0", name="ck_agent_confirmation_version_nonnegative"),
        sa.CheckConstraint(
            "authorization_status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED')",
            name="ck_agent_confirmation_authorization_status",
        ),
        sa.CheckConstraint(
            "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL')",
            name="ck_agent_confirmation_execution_status",
        ),
        sa.UniqueConstraint("request_id", name="uq_agent_confirmation_request"),
        sa.UniqueConstraint("decision_request_id", name="uq_agent_confirmation_decision_request"),
        sa.UniqueConstraint("credential_jti", name="uq_agent_confirmation_credential_jti"),
        comment="Agent 写操作授权和执行状态，不保存确认 Token",
    )
    op.create_index(
        "ix_agent_confirmation_pending",
        "agent_action_confirmations",
        ["subject_user_id", "authorization_status", "expires_at"],
    )
    op.create_index(
        "ix_agent_confirmation_thread",
        "agent_action_confirmations",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "ix_agent_confirmation_resource",
        "agent_action_confirmations",
        ["organization_id", "resource_type", "resource_id", "created_at"],
    )

    op.create_table(
        "agent_action_confirmation_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="事件序号"),
        sa.Column(
            "confirmation_id",
            sa.Text(),
            sa.ForeignKey("agent_action_confirmations.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属确认单",
        ),
        sa.Column("event_type", sa.Text(), nullable=False, comment="确认单事件类型"),
        sa.Column("actor_user_id", sa.Text(), nullable=True, comment="事件操作者主体标识"),
        sa.Column("request_id", sa.Text(), nullable=True, comment="业务请求幂等标识"),
        sa.Column("decision_request_id", sa.Text(), nullable=True, comment="确认决定幂等标识"),
        sa.Column("trace_id", sa.Text(), nullable=True, comment="跨服务链路标识"),
        sa.Column(
            "authorization_status", sa.Text(), nullable=False, comment="事件发生后的授权状态"
        ),
        sa.Column("execution_status", sa.Text(), nullable=False, comment="事件发生后的执行状态"),
        sa.Column(
            "authorization_version", sa.Integer(), nullable=False, comment="事件对应的确认单版本"
        ),
        sa.Column(
            "actor_roles",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="操作者角色快照",
        ),
        sa.Column(
            "actor_organization_ids",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="操作者机构范围快照",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="事件时间",
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED', 'ISSUED', 'CLAIMED', 'CONSUMED', 'REQUEUED', 'EXECUTION_SUCCEEDED', 'EXECUTION_FAILED')",
            name="ck_agent_confirmation_event_type",
        ),
        sa.CheckConstraint(
            "authorization_status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED')",
            name="ck_agent_confirmation_event_authorization_status",
        ),
        sa.CheckConstraint(
            "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL')",
            name="ck_agent_confirmation_event_execution_status",
        ),
        comment="Agent 写操作确认不可变事件审计，不保存 Token 和明文参数",
    )
    op.create_index(
        "ix_agent_confirmation_events_confirmation",
        "agent_action_confirmation_events",
        ["confirmation_id", "created_at", "id"],
    )


def downgrade() -> None:
    """删除确认事件和确认单表。"""

    op.drop_index(
        "ix_agent_confirmation_events_confirmation", table_name="agent_action_confirmation_events"
    )
    op.drop_table("agent_action_confirmation_events")
    op.drop_index("ix_agent_confirmation_resource", table_name="agent_action_confirmations")
    op.drop_index("ix_agent_confirmation_thread", table_name="agent_action_confirmations")
    op.drop_index("ix_agent_confirmation_pending", table_name="agent_action_confirmations")
    op.drop_table("agent_action_confirmations")
