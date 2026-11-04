"""Link reports to authorized training items and audit ownership recovery."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0060"
down_revision: str | None = "20261104_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_report_training_links",
        sa.Column(
            "report_id",
            sa.Text(),
            sa.ForeignKey("agent_movement_reports.report_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("day_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_report_training_plan", "agent_report_training_links", ["plan_id", "day_id", "item_id"]
    )
    op.create_table(
        "agent_recovery_audit",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("actor_hash", sa.Text(), nullable=False),
        sa.Column("conversation_hash", sa.Text(), nullable=False),
        sa.Column("previous_owner_hash", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "outcome IN ('RECOVERED','NOOP','UNAVAILABLE','CONFLICT','INTEGRITY_ERROR')",
            name="ck_recovery_audit_outcome",
        ),
    )
    op.create_index("ix_recovery_audit_retention", "agent_recovery_audit", ["created_at"])
    op.create_index("ix_recovery_audit_actor", "agent_recovery_audit", ["actor_hash", "created_at"])
    op.create_table(
        "agent_lineage_repair_audit",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("operator_hash", sa.Text(), nullable=False),
        sa.Column("conversation_hash", sa.Text(), nullable=False),
        sa.Column("affected_runs", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("affected_runs > 0", name="ck_lineage_repair_runs"),
    )
    op.create_index(
        "ix_lineage_repair_audit_retention", "agent_lineage_repair_audit", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("agent_lineage_repair_audit")
    op.drop_table("agent_recovery_audit")
    op.drop_table("agent_report_training_links")
