"""增加可审核、可重试的知识上传和索引任务。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0003"
down_revision: str | None = "20260812_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建持久化任务状态，但不让上传内容自动变为可检索内容。"""

    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("allowed_roles", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_version", sa.Integer(), nullable=False),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("reviewer_id", sa.Text(), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "visibility IN ('GLOBAL', 'ORGANIZATION', 'PRIVATE')",
            name="ck_ingestion_jobs_visibility",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW', 'QUEUED', 'INDEXING', 'SUCCEEDED', 'FAILED', 'REJECTED')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_ingestion_jobs_size_positive"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0", name="ck_ingestion_jobs_attempts"
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_status",
        "knowledge_ingestion_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_source",
        "knowledge_ingestion_jobs",
        ["source_uri", "requested_version"],
    )


def downgrade() -> None:
    """删除上传/索引任务状态，同时保留可检索知识表。"""

    op.drop_index("ix_knowledge_ingestion_jobs_source", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_ingestion_jobs_status", table_name="knowledge_ingestion_jobs")
    op.drop_table("knowledge_ingestion_jobs")
