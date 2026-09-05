"""为知识上传任务增加客户端幂等和同作用域并发保护。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0037"
down_revision: str | None = "20260905_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """让客户端重试可以复用原任务，并用数据库唯一索引兜底并发请求。"""

    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    # 幂等键只在同一提交者、同一知识作用域内唯一；NULL 表示旧客户端未提供键。
    # COALESCE 让 PostgreSQL 的 NULL 作用域也参与唯一约束。
    op.execute(
        """
        CREATE UNIQUE INDEX uq_knowledge_ingestion_jobs_idempotency
        ON knowledge_ingestion_jobs (
            submitted_by,
            visibility,
            COALESCE(organization_id, ''),
            COALESCE(owner_user_id, ''),
            idempotency_key
        )
        WHERE idempotency_key IS NOT NULL
        """
    )
    # 即使两个请求在应用层“先查都为空”，同一 source 作用域也不能同时存在多个
    # PENDING_REVIEW/QUEUED/INDEXING 任务。任务完成或拒绝后会自动离开该唯一索引。
    op.execute(
        """
        CREATE UNIQUE INDEX uq_knowledge_ingestion_jobs_active_source_scope
        ON knowledge_ingestion_jobs (
            source_uri,
            visibility,
            COALESCE(organization_id, ''),
            COALESCE(owner_user_id, '')
        )
        WHERE status IN ('PENDING_REVIEW', 'QUEUED', 'INDEXING')
        """
    )
    # 同一 source 作用域的活跃任务查询路径，支持先读后由唯一索引兜底竞态。
    op.create_index(
        "ix_knowledge_ingestion_jobs_source_scope_status",
        "knowledge_ingestion_jobs",
        ["source_uri", "visibility", "organization_id", "owner_user_id", "status", "created_at"],
    )


def downgrade() -> None:
    """删除幂等约束和查询索引，不删除历史任务。"""

    op.drop_index(
        "ix_knowledge_ingestion_jobs_source_scope_status",
        table_name="knowledge_ingestion_jobs",
    )
    op.execute("DROP INDEX uq_knowledge_ingestion_jobs_active_source_scope")
    op.execute("DROP INDEX uq_knowledge_ingestion_jobs_idempotency")
    op.drop_column("knowledge_ingestion_jobs", "idempotency_key")
