"""Record deterministic file safety checks for knowledge ingestion jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0004"
down_revision: str | None = "20260812_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store content identity and the scanner verdict used before staging."""

    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column("content_sha256", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column(
            "safety_status",
            sa.Text(),
            nullable=False,
            server_default="STRUCTURAL_VALIDATED",
        ),
    )
    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column("scanner_name", sa.Text(), nullable=False, server_default="structural-v1"),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_sha256",
        "knowledge_ingestion_jobs",
        ["content_sha256"],
    )


def downgrade() -> None:
    """Remove safety metadata while retaining the task lifecycle."""

    op.drop_index("ix_knowledge_ingestion_jobs_sha256", table_name="knowledge_ingestion_jobs")
    op.drop_column("knowledge_ingestion_jobs", "scanner_name")
    op.drop_column("knowledge_ingestion_jobs", "safety_status")
    op.drop_column("knowledge_ingestion_jobs", "content_sha256")
