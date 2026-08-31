"""增加用于重建已发布知识索引的持久化批次任务。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0006"
down_revision: str | None = "20260812_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建批次和条目状态，以支持可复现、可恢复的索引重建。"""

    op.create_table(
        "knowledge_reindex_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("target_document_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=False),
        sa.Column("processed_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'INDEXING', 'SUCCEEDED', 'FAILED')",
            name="ck_knowledge_reindex_jobs_status",
        ),
        sa.CheckConstraint("total_documents > 0", name="ck_knowledge_reindex_jobs_total_positive"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name="ck_knowledge_reindex_jobs_attempts",
        ),
    )
    op.create_index(
        "ix_knowledge_reindex_jobs_status",
        "knowledge_reindex_jobs",
        ["status", "created_at"],
    )

    op.create_table(
        "knowledge_reindex_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("knowledge_reindex_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'INDEXING', 'SUCCEEDED', 'SKIPPED', 'FAILED')",
            name="ck_knowledge_reindex_items_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name="ck_knowledge_reindex_items_attempts",
        ),
        sa.UniqueConstraint("job_id", "document_id", name="uq_knowledge_reindex_items_document"),
    )
    op.create_index(
        "ix_knowledge_reindex_items_queue",
        "knowledge_reindex_items",
        ["job_id", "status", "created_at"],
    )


def downgrade() -> None:
    """删除重建任务状态，但不修改可检索知识。"""

    op.drop_index("ix_knowledge_reindex_items_queue", table_name="knowledge_reindex_items")
    op.drop_table("knowledge_reindex_items")
    op.drop_index("ix_knowledge_reindex_jobs_status", table_name="knowledge_reindex_jobs")
    op.drop_table("knowledge_reindex_jobs")
