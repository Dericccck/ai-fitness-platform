"""在每个索引重建条目中保存不可变的文档元数据。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0007"
down_revision: str | None = "20260812_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """让重建条目不受后续文档版本变化影响。"""

    # 0006 曾在本地开发中短暂执行，而当时快照结构仍在定稿。IF NOT EXISTS 让这次只前进
    # 的迁移同时兼容该数据库和根据已提交历史全新安装的数据库。
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS source_uri TEXT")
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS title TEXT")
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS document_type TEXT")
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS organization_id TEXT")
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS owner_user_id TEXT")
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS visibility TEXT")
    op.execute(
        "ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS allowed_roles TEXT[] DEFAULT '{}'"
    )
    op.execute(
        "ALTER TABLE knowledge_reindex_items "
        "ADD COLUMN IF NOT EXISTS effective_from TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE knowledge_reindex_items "
        "ADD COLUMN IF NOT EXISTS effective_to TIMESTAMP WITH TIME ZONE"
    )
    op.execute("ALTER TABLE knowledge_reindex_items ADD COLUMN IF NOT EXISTS version INTEGER")
    op.execute(
        """
        UPDATE knowledge_reindex_items i
        SET source_uri = d.source_uri,
            title = d.title,
            document_type = d.document_type,
            organization_id = d.organization_id,
            owner_user_id = j.owner_user_id,
            visibility = d.visibility,
            allowed_roles = d.applicable_roles,
            effective_from = d.effective_from,
            effective_to = d.effective_to,
            version = d.version
        FROM knowledge_documents d
        LEFT JOIN LATERAL (
            SELECT owner_user_id
            FROM knowledge_ingestion_jobs
            WHERE document_id = d.id AND status = 'SUCCEEDED'
            ORDER BY finished_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        ) j ON TRUE
        WHERE i.document_id = d.id
        """
    )
    for column in (
        "source_uri",
        "title",
        "document_type",
        "visibility",
        "effective_from",
        "version",
    ):
        op.alter_column("knowledge_reindex_items", column, nullable=False)
    op.alter_column("knowledge_reindex_items", "allowed_roles", nullable=False)


def downgrade() -> None:
    """删除快照字段，同时保留原有重建任务状态。"""

    op.drop_column("knowledge_reindex_items", "version")
    op.drop_column("knowledge_reindex_items", "effective_to")
    op.drop_column("knowledge_reindex_items", "effective_from")
    op.drop_column("knowledge_reindex_items", "allowed_roles")
    op.drop_column("knowledge_reindex_items", "visibility")
    op.drop_column("knowledge_reindex_items", "owner_user_id")
    op.drop_column("knowledge_reindex_items", "organization_id")
    op.drop_column("knowledge_reindex_items", "document_type")
    op.drop_column("knowledge_reindex_items", "title")
    op.drop_column("knowledge_reindex_items", "source_uri")
