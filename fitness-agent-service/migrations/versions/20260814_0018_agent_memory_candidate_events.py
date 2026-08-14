"""新增 Memory 候选生命周期不可变审计事件。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0018"
down_revision: str | None = "20260814_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录候选创建、批准、拒绝和过期，不保存正文或确认凭证。"""

    op.create_table(
        "agent_memory_candidate_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="事件序号"),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("agent_memory_candidates.id", ondelete="RESTRICT"),
            nullable=False,
            comment="关联的 Memory 候选",
        ),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="候选所属用户快照"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="候选所属机构快照"),
        sa.Column(
            "event_type",
            sa.Text(),
            nullable=False,
            comment="事件类型：CREATED 创建、APPROVED 批准、REJECTED 拒绝、EXPIRED 过期",
        ),
        sa.Column(
            "actor_type",
            sa.Text(),
            nullable=False,
            comment="操作者类型：AGENT 候选提取、USER 用户决定、SYSTEM 定时清理",
        ),
        sa.Column("actor_user_id", sa.Text(), nullable=True, comment="用户决定时的操作者主体"),
        sa.Column(
            "status_after",
            sa.Text(),
            nullable=False,
            comment="事件完成后的候选状态快照",
        ),
        sa.Column("request_id", sa.Text(), nullable=False, comment="触发事件的请求或系统任务标识"),
        sa.Column(
            "decision_request_id",
            sa.Text(),
            nullable=True,
            comment="批准/拒绝决定请求标识",
        ),
        sa.Column("payload_hash", sa.Text(), nullable=False, comment="候选正文摘要，不保存正文"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="事件发生时间",
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_agent_memory_candidate_events_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('AGENT', 'USER', 'SYSTEM')",
            name="ck_agent_memory_candidate_events_actor",
        ),
        sa.CheckConstraint(
            "status_after IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_agent_memory_candidate_events_status",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_agent_memory_candidate_events_hash",
        ),
    )
    op.create_index(
        "ix_agent_memory_candidate_events_candidate_created",
        "agent_memory_candidate_events",
        ["candidate_id", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_memory_candidate_events_subject_scope_created",
        "agent_memory_candidate_events",
        ["subject_user_id", "organization_id", "created_at"],
    )
    op.create_table_comment(
        "agent_memory_candidate_events",
        "Memory 候选生命周期不可变审计事件；只保存主体、状态和正文摘要，不保存候选明文或确认凭证",
    )


def downgrade() -> None:
    """删除候选审计事件表。"""

    op.drop_index(
        "ix_agent_memory_candidate_events_subject_scope_created",
        table_name="agent_memory_candidate_events",
    )
    op.drop_index(
        "ix_agent_memory_candidate_events_candidate_created",
        table_name="agent_memory_candidate_events",
    )
    op.drop_table("agent_memory_candidate_events")
