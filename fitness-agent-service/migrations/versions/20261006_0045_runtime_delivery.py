"""模型用量尝试账本和加密的流式任务终态；不存输入正文或签名身份。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0045"
down_revision: str | None = "20260918_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_model_usage",
        sa.Column("call_id", sa.Text(), primary_key=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text()),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("cache_hit_tokens", sa.BigInteger()),
        sa.Column("cache_miss_tokens", sa.BigInteger()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("ttft_seconds", sa.Float()),
        sa.Column("estimated_cost", sa.Numeric(24, 12)),
        sa.Column("currency", sa.Text()),
        sa.Column("pricing_version", sa.Text()),
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
            "status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')", name="ck_model_usage_status"
        ),
        sa.CheckConstraint("attempt > 0", name="ck_model_usage_attempt"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_hit_tokens >= 0 AND cache_miss_tokens >= 0 AND estimated_cost >= 0",
            name="ck_model_usage_nonnegative",
        ),
    )
    op.create_index("ix_model_usage_request", "agent_model_usage", ["request_id", "created_at"])
    op.create_index(
        "ix_model_usage_run",
        "agent_model_usage",
        ["run_id", "created_at"],
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index("ix_model_usage_retention", "agent_model_usage", ["created_at"])
    op.create_table(
        "agent_chat_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("result_ciphertext", sa.LargeBinary()),
        sa.Column("key_version", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("execution_deadline", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('RUNNING','COMPLETED','FAILED','UNKNOWN')", name="ck_chat_run_status"
        ),
        sa.CheckConstraint(
            "(result_ciphertext IS NULL) = (key_version IS NULL)", name="ck_chat_run_cipher"
        ),
        sa.CheckConstraint(
            "status != 'COMPLETED' OR result_ciphertext IS NOT NULL", name="ck_chat_run_result"
        ),
    )
    op.create_index("ix_chat_run_retention", "agent_chat_runs", ["expires_at"])
    op.create_index(
        "ix_chat_run_deadline",
        "agent_chat_runs",
        ["execution_deadline"],
        postgresql_where=sa.text("status='RUNNING'"),
    )


def downgrade() -> None:
    op.drop_table("agent_chat_runs")
    op.drop_table("agent_model_usage")
