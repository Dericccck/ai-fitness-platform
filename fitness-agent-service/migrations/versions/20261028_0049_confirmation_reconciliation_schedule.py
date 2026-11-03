"""持久化全域对账调度与租约，永久 UNKNOWN 不再阻塞后续动作。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0049"
down_revision: str | None = "20261027_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in (
        "reconciliation_next_attempt_at",
        "reconciliation_last_attempt_at",
        "reconciliation_lease_until",
    ):
        op.add_column("agent_action_confirmations", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("agent_action_confirmations", sa.Column("reconciliation_claim_token", sa.Text()))
    op.add_column(
        "agent_action_confirmations",
        sa.Column("reconciliation_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_confirmation_reconciliation_attempts",
        "agent_action_confirmations",
        "reconciliation_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_confirmation_reconciliation_lease",
        "agent_action_confirmations",
        "(reconciliation_claim_token IS NULL AND reconciliation_lease_until IS NULL) OR "
        "(reconciliation_claim_token IS NOT NULL AND reconciliation_lease_until IS NOT NULL)",
    )
    op.create_index(
        "ix_confirmation_reconciliation_due",
        "agent_action_confirmations",
        [
            sa.text(
                "COALESCE(reconciliation_next_attempt_at, execution_started_at, finished_at, created_at)"
            ),
            "id",
        ],
        postgresql_where=sa.text("execution_status IN ('RUNNING','UNKNOWN','FAILED_RETRYABLE')"),
    )


def downgrade() -> None:
    # 租约只保护无副作用的结果查询；业务 ID、确认、执行状态和 UNKNOWN 审计均保留。
    op.drop_index("ix_confirmation_reconciliation_due", table_name="agent_action_confirmations")
    op.drop_constraint(
        "ck_confirmation_reconciliation_lease", "agent_action_confirmations", type_="check"
    )
    op.drop_constraint(
        "ck_confirmation_reconciliation_attempts", "agent_action_confirmations", type_="check"
    )
    for name in (
        "reconciliation_attempts",
        "reconciliation_claim_token",
        "reconciliation_lease_until",
        "reconciliation_last_attempt_at",
        "reconciliation_next_attempt_at",
    ):
        op.drop_column("agent_action_confirmations", name)
