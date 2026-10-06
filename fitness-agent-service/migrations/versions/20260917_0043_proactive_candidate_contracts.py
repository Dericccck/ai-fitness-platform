"""收紧主动候选类型和来源契约。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260917_0043"
down_revision: str | None = "20260917_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_agent_proactive_candidate_type",
        "agent_proactive_candidates",
        "candidate_type IN ('TRAINING_ADHERENCE','TRAINING_PLAN_MISMATCH','TRAINING_CYCLE_REVIEW','COACH_STUDENT_ATTENTION','COACH_DAILY_DIGEST','OPERATIONS_DAILY_DIGEST','APPOINTMENT_FOLLOW_UP','CUSTOMER_SERVICE_FOLLOW_UP')",
    )
    op.create_check_constraint(
        "ck_agent_proactive_candidate_trigger_source",
        "agent_proactive_candidates",
        "trigger_source IN ('EVENT','SCHEDULE','MANUAL','USER_RESPONSE')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_proactive_candidate_trigger_source",
        "agent_proactive_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proactive_candidate_type",
        "agent_proactive_candidates",
        type_="check",
    )
