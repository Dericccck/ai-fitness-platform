"""新增正式 Memory 生命周期不可变审计事件。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0019"
down_revision: str | None = "20260814_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录正式 Memory 的保存、撤销和自动过期，不复制 Memory 正文。"""

    op.create_table(
        "agent_memory_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="事件序号"),
        sa.Column(
            "memory_id",
            sa.Text(),
            sa.ForeignKey("agent_memories.id", ondelete="RESTRICT"),
            nullable=False,
            comment="关联的正式 Memory",
        ),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="Memory 所属用户快照"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="Memory 所属机构快照"),
        sa.Column(
            "event_type",
            sa.Text(),
            nullable=False,
            comment="事件类型：SAVED 保存、REVOKED 撤销、EXPIRED 自动过期",
        ),
        sa.Column(
            "actor_type",
            sa.Text(),
            nullable=False,
            comment="操作者类型：AGENT 工具/候选晋级、USER 用户明确操作、SYSTEM 定时任务",
        ),
        sa.Column("actor_user_id", sa.Text(), nullable=True, comment="用户操作时的主体快照"),
        sa.Column("status_after", sa.Text(), nullable=False, comment="事件后的 Memory 状态"),
        sa.Column("version_after", sa.Integer(), nullable=False, comment="事件后的乐观锁版本"),
        sa.Column("request_id", sa.Text(), nullable=False, comment="请求或系统任务标识"),
        sa.Column(
            "operation_id",
            sa.Text(),
            nullable=False,
            comment="写操作幂等键；相同操作重试不能重复改变状态",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="事件发生时间",
        ),
        sa.CheckConstraint(
            "event_type IN ('SAVED', 'REVOKED', 'EXPIRED')",
            name="ck_agent_memory_events_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('AGENT', 'USER', 'SYSTEM')",
            name="ck_agent_memory_events_actor",
        ),
        sa.CheckConstraint(
            "status_after IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
            name="ck_agent_memory_events_status",
        ),
        sa.CheckConstraint("version_after >= 1", name="ck_agent_memory_events_version"),
        sa.UniqueConstraint("operation_id", name="uq_agent_memory_events_operation"),
    )
    op.create_index(
        "ix_agent_memory_events_memory_created",
        "agent_memory_events",
        ["memory_id", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_memory_events_subject_scope_created",
        "agent_memory_events",
        ["subject_user_id", "organization_id", "created_at"],
    )
    op.create_table_comment(
        "agent_memory_events",
        "正式 Memory 生命周期不可变审计事件；只保存状态和版本快照，不保存 Memory 正文",
    )


def downgrade() -> None:
    """删除正式 Memory 审计事件表。"""

    op.drop_index("ix_agent_memory_events_subject_scope_created", table_name="agent_memory_events")
    op.drop_index("ix_agent_memory_events_memory_created", table_name="agent_memory_events")
    op.drop_table("agent_memory_events")
