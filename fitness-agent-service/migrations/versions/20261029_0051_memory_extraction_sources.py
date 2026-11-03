"""保存加密的候选提取来源与可重试任务。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0051"
down_revision: str | None = "20261028_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_source_messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("source_request_id", sa.Text(), nullable=False),
        sa.Column("message_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Text(), nullable=False),
        sa.Column("write_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="CAPTURED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_user_id", "organization_id", "thread_id", "source_request_id", name="uq_memory_source_request"),
        sa.CheckConstraint("status IN ('CAPTURED','COMPLETED')", name="ck_memory_source_status"),
    )
    op.create_index("ix_memory_source_retention", "agent_memory_source_messages", ["retain_until"])
    op.create_table(
        "agent_memory_extraction_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("source_request_id", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.Text(), sa.ForeignKey("agent_memory_source_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("write_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_token", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subject_user_id", "organization_id", "source_message_id", name="uq_memory_extraction_source"),
        sa.CheckConstraint("status IN ('PENDING','RUNNING','DONE','FAILED')", name="ck_memory_extraction_job_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_memory_extraction_attempts"),
    )
    op.create_index("ix_memory_extraction_ready", "agent_memory_extraction_jobs", ["status", "available_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_memory_extraction_ready", table_name="agent_memory_extraction_jobs")
    op.drop_table("agent_memory_extraction_jobs")
    op.drop_index("ix_memory_source_retention", table_name="agent_memory_source_messages")
    op.drop_table("agent_memory_source_messages")
