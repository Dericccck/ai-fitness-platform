"""增加经营目标、异常、决策、动作与效果归因主链路。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_0044"
down_revision: str | None = "20260917_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_business_objectives",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("objective_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("target_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("guardrail_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("organization_id", "objective_key", name="uq_business_objective_key"),
        sa.CheckConstraint(
            "direction IN ('INCREASE','DECREASE','TARGET')", name="ck_business_objective_direction"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')", name="ck_business_objective_status"
        ),
        sa.CheckConstraint("version > 0", name="ck_business_objective_version"),
    )
    op.create_index(
        "ix_business_objective_org", "agent_business_objectives", ["organization_id", "status"]
    )

    op.create_table(
        "agent_strategy_policies",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column(
            "objective_id",
            sa.Text(),
            sa.ForeignKey("agent_business_objectives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("strategy_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False, server_default="SHADOW"),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_actions", postgresql.JSONB(), nullable=False),
        sa.Column("budget_limit", sa.Numeric(20, 4), nullable=True),
        sa.Column("max_affected_subjects", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "organization_id", "strategy_key", "version", name="uq_strategy_policy_version"
        ),
        sa.CheckConstraint(
            "mode IN ('DISABLED','REPLAY','SHADOW','ADVISORY','AUTONOMOUS')",
            name="ck_strategy_policy_mode",
        ),
        sa.CheckConstraint(
            "risk_level IN ('L0','L1','L2','L3','L4')", name="ck_strategy_policy_risk"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')", name="ck_strategy_policy_status"
        ),
        sa.CheckConstraint(
            "version > 0 AND max_affected_subjects > 0", name="ck_strategy_policy_limits"
        ),
    )
    op.create_index(
        "ix_strategy_policy_active",
        "agent_strategy_policies",
        ["organization_id", "status", "valid_from"],
    )

    op.create_table(
        "agent_metric_snapshots",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("dimension_type", sa.Text(), nullable=False, server_default="ORGANIZATION"),
        sa.Column("dimension_id", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False, server_default="PASSED"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "metric",
            "dimension_type",
            "dimension_id",
            "bucket",
            "period_start",
            "catalog_version",
            name="uq_metric_snapshot_scope",
        ),
        sa.CheckConstraint("bucket IN ('DAY','WEEK','MONTH')", name="ck_metric_snapshot_bucket"),
        sa.CheckConstraint("sample_size >= 0", name="ck_metric_snapshot_sample"),
        sa.CheckConstraint("period_end > period_start", name="ck_metric_snapshot_period"),
        sa.CheckConstraint(
            "quality_status IN ('PASSED','STALE','INCOMPLETE','REJECTED')",
            name="ck_metric_snapshot_quality",
        ),
    )
    op.create_index(
        "ix_metric_snapshot_series",
        "agent_metric_snapshots",
        ["organization_id", "metric", "period_start"],
    )

    op.create_table(
        "agent_anomaly_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column(
            "objective_id",
            sa.Text(),
            sa.ForeignKey("agent_business_objectives.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Text(),
            sa.ForeignKey("agent_strategy_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "metric_snapshot_id",
            sa.Text(),
            sa.ForeignKey("agent_metric_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("anomaly_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("actual_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("expected_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("deviation_percent", sa.Numeric(12, 4), nullable=True),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
        sa.Column("dedupe_key", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "anomaly_type IN ('THRESHOLD','TARGET_GAP','STATISTICAL','TREND','FORECAST')",
            name="ck_anomaly_type",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_anomaly_severity"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED','DATA_INSUFFICIENT')",
            name="ck_anomaly_status",
        ),
    )
    op.create_index(
        "ix_anomaly_org_status",
        "agent_anomaly_events",
        ["organization_id", "status", "detected_at"],
    )

    op.create_table(
        "agent_decision_cases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column(
            "objective_id",
            sa.Text(),
            sa.ForeignKey("agent_business_objectives.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Text(),
            sa.ForeignKey("agent_strategy_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "anomaly_id",
            sa.Text(),
            sa.ForeignKey("agent_anomaly_events.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="DETECTED"),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("diagnosis", postgresql.JSONB(), nullable=False),
        sa.Column("selected_option_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('DETECTED','DIAGNOSING','OPTIONS_READY','POLICY_EVALUATED','WAITING_APPROVAL','APPROVED','EXECUTING','OBSERVING','EVALUATED','CLOSED','DATA_INSUFFICIENT','POLICY_REJECTED','APPROVAL_REJECTED','EXECUTION_FAILED','SUSPENDED','EXPIRED')",
            name="ck_decision_case_status",
        ),
        sa.CheckConstraint(
            "risk_level IN ('L0','L1','L2','L3','L4')", name="ck_decision_case_risk"
        ),
    )
    op.create_index(
        "ix_decision_case_org", "agent_decision_cases", ["organization_id", "status", "created_at"]
    )

    op.create_table(
        "agent_decision_case_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_decision_case_event", "agent_decision_case_events", ["case_id", "created_at"]
    )

    op.create_table(
        "agent_decision_options",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("option_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False),
        sa.Column("expected_benefit", sa.Numeric(20, 4), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("risk_penalty", sa.Numeric(20, 4), nullable=False),
        sa.Column("uncertainty_penalty", sa.Numeric(20, 4), nullable=False),
        sa.Column("score", sa.Numeric(20, 4), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("case_id", "option_key", name="uq_decision_option_key"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_decision_option_confidence"
        ),
        sa.CheckConstraint(
            "estimated_cost >= 0 AND risk_penalty >= 0 AND uncertainty_penalty >= 0",
            name="ck_decision_option_costs",
        ),
    )
    op.create_index("ix_decision_option_case", "agent_decision_options", ["case_id", "score"])

    op.create_table(
        "agent_decision_approvals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "option_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_options.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decision_request_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("case_id", "decision_request_id", name="uq_decision_approval_request"),
        sa.CheckConstraint("decision IN ('APPROVE','REJECT')", name="ck_decision_approval"),
    )

    op.create_table(
        "agent_action_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "option_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_options.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('PLANNED','AUTHORIZED','QUEUED','RUNNING','SUCCEEDED','RETRYABLE_FAILED','FINAL_FAILED','COMPENSATION_REQUIRED','COMPENSATED','CANCELLED')",
            name="ck_action_run_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_action_run_attempts"),
    )
    op.create_index(
        "ix_action_run_claim", "agent_action_runs", ["status", "available_at", "created_at"]
    )
    op.create_index(
        "ix_action_run_org", "agent_action_runs", ["organization_id", "status", "created_at"]
    )

    op.create_table(
        "agent_operations_tasks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "action_run_id",
            sa.Text(),
            sa.ForeignKey("agent_action_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
        sa.Column("assignee_id", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','COMPLETED','CANCELLED')",
            name="ck_operations_task_status",
        ),
    )
    op.create_index(
        "ix_operations_task_org",
        "agent_operations_tasks",
        ["organization_id", "status", "created_at"],
    )

    op.create_table(
        "agent_experiments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column(
            "policy_id",
            sa.Text(),
            sa.ForeignKey("agent_strategy_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("treatment_percent", sa.Integer(), nullable=False),
        sa.Column("primary_metric", sa.Text(), nullable=False),
        sa.Column("guardrail_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("observation_days", sa.Integer(), nullable=False),
        sa.Column("salt", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','RUNNING','STOPPED','COMPLETED')", name="ck_experiment_status"
        ),
        sa.CheckConstraint("treatment_percent BETWEEN 1 AND 99", name="ck_experiment_treatment"),
        sa.CheckConstraint("observation_days BETWEEN 1 AND 365", name="ck_experiment_days"),
    )

    op.create_table(
        "agent_experiment_assignments",
        sa.Column(
            "experiment_id",
            sa.Text(),
            sa.ForeignKey("agent_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_key_hash", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "experiment_id", "subject_key_hash", name="pk_experiment_assignment"
        ),
        sa.CheckConstraint("group_name IN ('TREATMENT','CONTROL')", name="ck_experiment_group"),
    )

    op.create_table(
        "agent_outcome_observations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("agent_decision_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "action_run_id",
            sa.Text(),
            sa.ForeignKey("agent_action_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "experiment_id",
            sa.Text(),
            sa.ForeignKey("agent_experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False, server_default="ALL"),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_digest", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "case_id",
            "metric",
            "group_name",
            "window_start",
            "window_end",
            name="uq_outcome_observation",
        ),
        sa.CheckConstraint("group_name IN ('ALL','TREATMENT','CONTROL')", name="ck_outcome_group"),
        sa.CheckConstraint(
            "sample_size >= 0 AND window_end > window_start", name="ck_outcome_window"
        ),
    )
    op.create_index(
        "ix_outcome_case", "agent_outcome_observations", ["case_id", "metric", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outcome_case", table_name="agent_outcome_observations")
    op.drop_table("agent_outcome_observations")
    op.drop_table("agent_experiment_assignments")
    op.drop_table("agent_experiments")
    op.drop_index("ix_operations_task_org", table_name="agent_operations_tasks")
    op.drop_table("agent_operations_tasks")
    op.drop_index("ix_action_run_org", table_name="agent_action_runs")
    op.drop_index("ix_action_run_claim", table_name="agent_action_runs")
    op.drop_table("agent_action_runs")
    op.drop_table("agent_decision_approvals")
    op.drop_index("ix_decision_option_case", table_name="agent_decision_options")
    op.drop_table("agent_decision_options")
    op.drop_index("ix_decision_case_event", table_name="agent_decision_case_events")
    op.drop_table("agent_decision_case_events")
    op.drop_index("ix_decision_case_org", table_name="agent_decision_cases")
    op.drop_table("agent_decision_cases")
    op.drop_index("ix_anomaly_org_status", table_name="agent_anomaly_events")
    op.drop_table("agent_anomaly_events")
    op.drop_index("ix_metric_snapshot_series", table_name="agent_metric_snapshots")
    op.drop_table("agent_metric_snapshots")
    op.drop_index("ix_strategy_policy_active", table_name="agent_strategy_policies")
    op.drop_table("agent_strategy_policies")
    op.drop_index("ix_business_objective_org", table_name="agent_business_objectives")
    op.drop_table("agent_business_objectives")
