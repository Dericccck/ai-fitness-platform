"""新增 Memory 正文保留期限、脱敏标记和自动清理字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0023"
down_revision: str | None = "20260814_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保留状态审计，但在期限到达后清除正式 Memory 和候选的正文。"""

    op.add_column(
        "agent_memories",
        sa.Column(
            "retention_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="REVOKED/EXPIRED 正文允许保留到的时间",
        ),
    )
    op.add_column(
        "agent_memories",
        sa.Column(
            "content_redacted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="正文是否已按保留策略不可逆脱敏",
        ),
    )
    op.add_column(
        "agent_memories",
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True, comment="正文脱敏时间"),
    )
    op.add_column(
        "agent_memories",
        sa.Column(
            "redaction_reason", sa.Text(), nullable=True, comment="脱敏原因，如 RETENTION_EXPIRED"
        ),
    )
    op.create_index(
        "ix_agent_memories_retention_due",
        "agent_memories",
        ["status", "retention_until", "content_redacted", "id"],
        postgresql_where=sa.text("content_redacted = false AND retention_until IS NOT NULL"),
    )
    # 迁移历史终态数据时使用默认期限，避免旧数据因为没有 retention_until 而永远不治理。
    op.execute(
        """
        UPDATE agent_memories
        SET retention_until = COALESCE(expires_at, updated_at) + INTERVAL '90 days'
        WHERE status IN ('REVOKED', 'EXPIRED')
          AND retention_until IS NULL
        """
    )
    op.drop_constraint("ck_agent_memory_events_type", "agent_memory_events", type_="check")
    op.create_check_constraint(
        "ck_agent_memory_events_type",
        "agent_memory_events",
        "event_type IN ('SAVED', 'REVOKED', 'EXPIRED', 'REDACTED')",
    )

    op.add_column(
        "agent_memory_candidates",
        sa.Column(
            "retention_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="终态候选密文允许保留到的时间",
        ),
    )
    op.add_column(
        "agent_memory_candidates",
        sa.Column(
            "payload_redacted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="候选正文密文是否已清空",
        ),
    )
    op.add_column(
        "agent_memory_candidates",
        sa.Column(
            "payload_redacted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="候选密文清理时间",
        ),
    )
    op.add_column(
        "agent_memory_candidates",
        sa.Column(
            "payload_redaction_reason",
            sa.Text(),
            nullable=True,
            comment="候选密文清理原因，如 RETENTION_EXPIRED",
        ),
    )
    op.create_index(
        "ix_agent_memory_candidates_retention_due",
        "agent_memory_candidates",
        ["status", "retention_until", "payload_redacted", "id"],
        postgresql_where=sa.text("payload_redacted = false AND retention_until IS NOT NULL"),
    )
    op.execute(
        """
        UPDATE agent_memory_candidates
        SET retention_until = updated_at + INTERVAL '30 days'
        WHERE status IN ('APPROVED', 'REJECTED', 'EXPIRED')
          AND retention_until IS NULL
        """
    )
    op.drop_constraint(
        "ck_agent_memory_candidate_events_type",
        "agent_memory_candidate_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_memory_candidate_events_type",
        "agent_memory_candidate_events",
        "event_type IN ('CREATED', 'APPROVED', 'REJECTED', 'EXPIRED', 'REDACTED')",
    )

    op.create_table_comment(
        "agent_memories",
        "用户明确提供并确认过的低敏健身长期 Memory；终态正文按保留策略脱敏，生命周期审计继续保留",
    )
    op.create_table_comment(
        "agent_memory_candidates",
        "模型提出但尚未得到用户确认的健身 Memory 候选；正文加密保存，终态密文按保留策略清空",
    )


def downgrade() -> None:
    """回滚正文治理字段；生产环境不建议在已发生脱敏后回滚。"""

    op.drop_constraint(
        "ck_agent_memory_candidate_events_type",
        "agent_memory_candidate_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_memory_candidate_events_type",
        "agent_memory_candidate_events",
        "event_type IN ('CREATED', 'APPROVED', 'REJECTED', 'EXPIRED')",
    )
    op.drop_index("ix_agent_memory_candidates_retention_due", table_name="agent_memory_candidates")
    op.drop_column("agent_memory_candidates", "payload_redaction_reason")
    op.drop_column("agent_memory_candidates", "payload_redacted_at")
    op.drop_column("agent_memory_candidates", "payload_redacted")
    op.drop_column("agent_memory_candidates", "retention_until")

    op.drop_constraint("ck_agent_memory_events_type", "agent_memory_events", type_="check")
    op.create_check_constraint(
        "ck_agent_memory_events_type",
        "agent_memory_events",
        "event_type IN ('SAVED', 'REVOKED', 'EXPIRED')",
    )
    op.drop_index("ix_agent_memories_retention_due", table_name="agent_memories")
    op.drop_column("agent_memories", "redaction_reason")
    op.drop_column("agent_memories", "redacted_at")
    op.drop_column("agent_memories", "content_redacted")
    op.drop_column("agent_memories", "retention_until")
