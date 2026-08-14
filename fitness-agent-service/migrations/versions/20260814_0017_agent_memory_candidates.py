"""新增加密的 Memory 候选表和待确认生命周期。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0017"
down_revision: str | None = "20260814_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存模型候选的最小元数据，并把候选正文以 AES-GCM 密文落库。

    候选正文不直接放入普通 JSONB：候选可能包含用户的训练偏好，且它还没有经过
    用户确认。类型、键、状态等可用于索引和状态机判断的字段保持明文，具体 value
    和 unit 放在应用层加密的 payload_ciphertext 中。
    """

    op.create_table(
        "agent_memory_candidates",
        sa.Column("id", sa.Text(), primary_key=True, comment="候选唯一标识"),
        sa.Column("subject_user_id", sa.Text(), nullable=False, comment="候选所属用户"),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="候选所属机构"),
        sa.Column(
            "memory_type",
            sa.Text(),
            nullable=False,
            comment="候选 Memory 类型；与正式 Memory 类型白名单一致",
        ),
        sa.Column("memory_key", sa.Text(), nullable=False, comment="候选 Memory 稳定业务键"),
        sa.Column("payload_hash", sa.Text(), nullable=False, comment="密文正文对应的 SHA-256 摘要"),
        sa.Column(
            "payload_ciphertext",
            sa.LargeBinary(),
            nullable=False,
            comment="候选正文 AES-GCM 密文，应用层解密",
        ),
        sa.Column(
            "payload_key_version",
            sa.Text(),
            nullable=False,
            comment="加密密钥版本，用于密钥轮换和解密路由",
        ),
        sa.Column(
            "source_thread_id",
            sa.Text(),
            nullable=False,
            comment="产生候选的脱敏会话标识，不保存原始会话信息",
        ),
        sa.Column(
            "source_request_id",
            sa.Text(),
            nullable=False,
            comment="产生候选的请求标识，用于追踪和故障定位",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PENDING",
            comment="候选状态：PENDING 待确认、APPROVED 已批准、REJECTED 已拒绝、EXPIRED 已过期",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="待确认有效期；过期后不能批准",
        ),
        sa.Column(
            "decision_request_id",
            sa.Text(),
            nullable=True,
            comment="用户决定请求标识，用于批准/拒绝操作幂等",
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True, comment="决定时间"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="候选创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="候选最近更新时间",
        ),
        sa.CheckConstraint(
            "memory_type IN ('TRAINING_GOAL', 'TRAINING_PREFERENCE', "
            "'EQUIPMENT_AVAILABILITY', 'SCHEDULE_PREFERENCE', 'COMMUNICATION_PREFERENCE')",
            name="ck_agent_memory_candidates_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_agent_memory_candidates_status",
        ),
        sa.CheckConstraint(
            "length(memory_key) BETWEEN 1 AND 64", name="ck_agent_memory_candidates_key"
        ),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_agent_memory_candidates_hash"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND decision_request_id IS NULL AND decided_at IS NULL) "
            "OR (status <> 'PENDING' AND decision_request_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_agent_memory_candidates_decision_fields",
        ),
    )
    op.create_index(
        "ix_agent_memory_candidates_pending_subject_scope",
        "agent_memory_candidates",
        ["subject_user_id", "organization_id", "status", "expires_at"],
    )
    op.create_index(
        "uq_agent_memory_candidates_pending_fingerprint",
        "agent_memory_candidates",
        ["subject_user_id", "organization_id", "memory_type", "memory_key", "payload_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_table_comment(
        "agent_memory_candidates",
        "模型提出但尚未得到用户确认的健身 Memory 候选；正文加密保存，批准后才晋级为正式 Memory",
    )


def downgrade() -> None:
    """删除候选表和对应索引。"""

    op.drop_index(
        "uq_agent_memory_candidates_pending_fingerprint",
        table_name="agent_memory_candidates",
    )
    op.drop_index(
        "ix_agent_memory_candidates_pending_subject_scope",
        table_name="agent_memory_candidates",
    )
    op.drop_table("agent_memory_candidates")
