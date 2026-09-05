"""增加确认执行结果未知状态，供对账流程使用。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260905_0039"
down_revision: str | None = "20260905_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_confirmation_execution_status", "agent_action_confirmations", type_="check")
    op.create_check_constraint(
        "ck_agent_confirmation_execution_status",
        "agent_action_confirmations",
        "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'UNKNOWN')",
    )
    op.drop_constraint("ck_agent_confirmation_event_execution_status", "agent_action_confirmation_events", type_="check")
    op.create_check_constraint(
        "ck_agent_confirmation_event_execution_status",
        "agent_action_confirmation_events",
        "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_confirmation_event_execution_status", "agent_action_confirmation_events", type_="check")
    op.create_check_constraint(
        "ck_agent_confirmation_event_execution_status",
        "agent_action_confirmation_events",
        "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL')",
    )
    op.drop_constraint("ck_agent_confirmation_execution_status", "agent_action_confirmations", type_="check")
    op.create_check_constraint(
        "ck_agent_confirmation_execution_status",
        "agent_action_confirmations",
        "execution_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL')",
    )
