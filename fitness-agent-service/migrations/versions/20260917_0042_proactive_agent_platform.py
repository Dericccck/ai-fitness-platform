"""增加主动式 Agent 的候选、决策、消息和反馈闭环。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260917_0042"
down_revision: str | None = "20260905_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROACTIVE_TYPES = (
    "'TRAINING_ADHERENCE_SUPPORT'",
    "'TRAINING_PLAN_ADJUSTMENT_SUGGESTED'",
    "'TRAINING_CYCLE_REVIEW'",
    "'COACH_DAILY_DIGEST'",
    "'COACH_STUDENT_ATTENTION'",
    "'OPERATIONS_DAILY_DIGEST'",
    "'APPOINTMENT_FOLLOW_UP'",
    "'CUSTOMER_SERVICE_FOLLOW_UP'",
)


def upgrade() -> None:
    op.add_column(
        "agent_proactive_event_inbox", sa.Column("last_replayed_by", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_proactive_event_inbox", sa.Column("last_replay_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_proactive_event_inbox",
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_agent_proactive_event_type", "agent_proactive_event_inbox", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_proactive_event_type",
        "agent_proactive_event_inbox",
        "event_type IN ('APPOINTMENT_CREATED','APPOINTMENT_RESCHEDULED','APPOINTMENT_CANCELLED','TRAINING_PLAN_PUBLISHED','TRAINING_PLAN_REVIEW_REQUIRED','TRAINING_DAY_COMPLETED','TRAINING_DAY_SKIPPED','TRAINING_PLAN_COMPLETED')",
    )
    op.execute(
        "CREATE INDEX ix_agent_proactive_event_student_time "
        "ON agent_proactive_event_inbox (organization_id, (payload->>'studentId'), created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_agent_proactive_event_coach_time "
        "ON agent_proactive_event_inbox (organization_id, (payload->>'coachId'), created_at DESC)"
    )
    op.create_table(
        "agent_proactive_candidates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("candidate_type", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Text(), nullable=False),
        sa.Column("trigger_source", sa.Text(), nullable=False),
        sa.Column("trigger_event_ids", postgresql.JSONB(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False, unique=True),
        sa.Column("priority", sa.Text(), nullable=False, server_default="NORMAL"),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replayed_by", sa.Text(), nullable=True),
        sa.Column("last_replay_reason", sa.Text(), nullable=True),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "priority IN ('LOW','NORMAL','HIGH')", name="ck_agent_proactive_candidate_priority"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','DEFERRED','DECIDED','IGNORED','ACTION_PLANNED','NOTIFICATION_QUEUED','COMPLETED','EXPIRED','RETRYABLE_FAILED','DEAD')",
            name="ck_agent_proactive_candidate_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_agent_proactive_candidate_attempts"),
    )
    op.create_index(
        "ix_agent_proactive_candidate_claim",
        "agent_proactive_candidates",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_agent_proactive_candidate_subject",
        "agent_proactive_candidates",
        ["subject_user_id", "organization_id", "created_at"],
    )

    op.create_table(
        "agent_proactive_context_snapshots",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("agent_proactive_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("context_version", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_agent_proactive_context_candidate",
        "agent_proactive_context_snapshots",
        ["candidate_id", "created_at"],
    )

    op.create_table(
        "agent_proactive_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("agent_proactive_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("output_digest", sa.Text(), nullable=False),
        sa.Column("safety_status", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "candidate_id", "decision_version", name="uq_agent_proactive_decision_version"
        ),
        sa.CheckConstraint(
            "decision IN ('IGNORE','DEFER','NOTIFY','ASK_USER','PROPOSE_ACTION','ESCALATE_TO_COACH')",
            name="ck_agent_proactive_decision",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_agent_proactive_confidence"
        ),
        sa.CheckConstraint(
            "safety_status IN ('PASSED','REJECTED','FALLBACK')", name="ck_agent_proactive_safety"
        ),
    )

    op.create_table(
        "agent_proactive_message_snapshots",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("agent_proactive_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False, server_default="IN_APP"),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta_type", sa.Text(), nullable=True),
        sa.Column(
            "cta_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("content_source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("candidate_id", "channel", name="uq_agent_proactive_message_channel"),
        sa.CheckConstraint(
            "channel IN ('IN_APP','PUSH','SMS','WECHAT')", name="ck_agent_proactive_message_channel"
        ),
        sa.CheckConstraint(
            "content_source IN ('TEMPLATE','LLM','FALLBACK')",
            name="ck_agent_proactive_content_source",
        ),
    )

    op.create_table(
        "agent_proactive_responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("agent_proactive_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notification_id", sa.Text(), nullable=True),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("response_type", sa.Text(), nullable=False),
        sa.Column("response_value", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "candidate_id", "subject_user_id", "response_type", name="uq_agent_proactive_response"
        ),
        sa.CheckConstraint(
            "response_type IN ('OPENED','ACCEPTED','DECLINED','SNOOZED','MUTED','HELPFUL','NOT_HELPFUL','OPENED_CHAT')",
            name="ck_agent_proactive_response_type",
        ),
    )

    op.create_table(
        "agent_proactive_subscriptions",
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Shanghai"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "subject_user_id", "organization_id", "audience", name="pk_agent_proactive_subscription"
        ),
        sa.CheckConstraint(
            "audience IN ('STUDENT','COACH','OPERATIONS')",
            name="ck_agent_proactive_subscription_audience",
        ),
    )

    op.add_column(
        "agent_in_app_notifications", sa.Column("proactive_candidate_id", sa.Text(), nullable=True)
    )
    op.add_column("agent_in_app_notifications", sa.Column("cta_type", sa.Text(), nullable=True))
    op.add_column(
        "agent_in_app_notifications", sa.Column("cta_payload", postgresql.JSONB(), nullable=True)
    )

    existing = (
        "'MEMORY_CANDIDATE_PENDING','APPOINTMENT_CREATED','APPOINTMENT_RESCHEDULED',"
        "'APPOINTMENT_CANCELLED','TRAINING_PLAN_PUBLISHED','TRAINING_PLAN_REVIEW_REQUIRED'"
    )
    all_types = existing + "," + ",".join(_PROACTIVE_TYPES)
    for table, constraint in (
        ("agent_notification_outbox", "ck_agent_notification_type"),
        ("agent_notification_preferences", "ck_agent_notification_preference_type"),
        ("agent_in_app_notifications", "ck_agent_in_app_notification_type"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"notification_type IN ({all_types})")


def downgrade() -> None:
    previous_types = (
        "'MEMORY_CANDIDATE_PENDING','APPOINTMENT_CREATED','APPOINTMENT_RESCHEDULED',"
        "'APPOINTMENT_CANCELLED','TRAINING_PLAN_PUBLISHED','TRAINING_PLAN_REVIEW_REQUIRED'"
    )
    proactive_types = ",".join(_PROACTIVE_TYPES)
    op.execute(
        sa.text(
            "DELETE FROM agent_notification_delivery_attempts WHERE outbox_id IN "
            f"(SELECT id FROM agent_notification_outbox WHERE notification_type IN ({proactive_types}))"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM agent_in_app_notifications WHERE notification_type IN ({proactive_types})"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM agent_notification_outbox WHERE notification_type IN ({proactive_types})"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM agent_notification_preferences WHERE notification_type IN ({proactive_types})"
        )
    )
    for table, constraint in (
        ("agent_notification_outbox", "ck_agent_notification_type"),
        ("agent_notification_preferences", "ck_agent_notification_preference_type"),
        ("agent_in_app_notifications", "ck_agent_in_app_notification_type"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"notification_type IN ({previous_types})")
    op.drop_column("agent_in_app_notifications", "cta_payload")
    op.drop_column("agent_in_app_notifications", "cta_type")
    op.drop_column("agent_in_app_notifications", "proactive_candidate_id")
    op.drop_table("agent_proactive_subscriptions")
    op.drop_table("agent_proactive_responses")
    op.drop_table("agent_proactive_message_snapshots")
    op.drop_table("agent_proactive_decisions")
    op.drop_index(
        "ix_agent_proactive_context_candidate",
        table_name="agent_proactive_context_snapshots",
    )
    op.drop_table("agent_proactive_context_snapshots")
    op.drop_index("ix_agent_proactive_candidate_subject", table_name="agent_proactive_candidates")
    op.drop_index("ix_agent_proactive_candidate_claim", table_name="agent_proactive_candidates")
    op.drop_table("agent_proactive_candidates")
    op.drop_index("ix_agent_proactive_event_coach_time", table_name="agent_proactive_event_inbox")
    op.drop_index("ix_agent_proactive_event_student_time", table_name="agent_proactive_event_inbox")
    op.drop_constraint(
        "ck_agent_proactive_event_type", "agent_proactive_event_inbox", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_proactive_event_type",
        "agent_proactive_event_inbox",
        "event_type IN ('APPOINTMENT_CREATED','APPOINTMENT_RESCHEDULED','APPOINTMENT_CANCELLED','TRAINING_PLAN_PUBLISHED','TRAINING_PLAN_REVIEW_REQUIRED')",
    )
    op.drop_column("agent_proactive_event_inbox", "replayed_at")
    op.drop_column("agent_proactive_event_inbox", "last_replay_reason")
    op.drop_column("agent_proactive_event_inbox", "last_replayed_by")
