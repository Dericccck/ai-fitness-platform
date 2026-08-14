"""新增用户可控的长期健身 Memory。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0015"
down_revision: str | None = "20260813_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建结构化 Memory 表和主体范围索引。

    当前版本只允许低敏健身偏好。约束放在数据库和应用两层，避免未来某个入口绕过
    Python Service 后写入未审核的医疗、支付或业务合同事实。
    """

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Text(), primary_key=True, comment="Memory 唯一标识"),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="Memory 所属用户"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="Memory 所属机构"),
        sa.Column(
            "memory_type",
            sa.Text(),
            nullable=False,
            comment="健身 Memory 类型，如训练目标、训练偏好、器械条件",
        ),
        sa.Column("memory_key", sa.Text(), nullable=False, comment="同类 Memory 的稳定业务键"),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="结构化内容，只允许 key/value/unit 等受控字段",
        ),
        sa.Column(
            "source_type",
            sa.Text(),
            nullable=False,
            server_default="USER_EXPLICIT",
            comment="来源类型；当前只接受用户明确提供",
        ),
        sa.Column(
            "confidence",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="1.0",
            comment="来源可信度；用户明确提供的事实为 1.0",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="ACTIVE",
            comment="生命周期：ACTIVE 可使用、REVOKED 已撤销、EXPIRED 已过期",
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1", comment="乐观锁版本"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="可选过期时间"),
        sa.Column(
            "source_request_id",
            sa.Text(),
            nullable=False,
            comment="确认执行请求 ID，用于审计和幂等定位",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="首次创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="最近修改时间",
        ),
        sa.CheckConstraint(
            "memory_type IN ('TRAINING_GOAL', 'TRAINING_PREFERENCE', "
            "'EQUIPMENT_AVAILABILITY', 'SCHEDULE_PREFERENCE', 'COMMUNICATION_PREFERENCE')",
            name="ck_agent_memories_type",
        ),
        sa.CheckConstraint("source_type = 'USER_EXPLICIT'", name="ck_agent_memories_source_type"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_agent_memories_confidence"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'EXPIRED')", name="ck_agent_memories_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_memories_version"),
        sa.UniqueConstraint(
            "subject_user_id",
            "organization_id",
            "memory_type",
            "memory_key",
            name="uq_agent_memories_subject_scope_key",
        ),
        sa.UniqueConstraint("source_request_id", name="uq_agent_memories_source_request"),
    )
    op.create_index(
        "ix_agent_memories_active_subject_scope",
        "agent_memories",
        ["subject_user_id", "organization_id", "status", "expires_at"],
    )


def downgrade() -> None:
    """删除长期 Memory 表；仅允许在回滚迁移时执行。"""

    op.drop_index("ix_agent_memories_active_subject_scope", table_name="agent_memories")
    op.drop_table("agent_memories")
