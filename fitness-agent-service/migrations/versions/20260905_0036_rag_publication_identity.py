"""补齐知识文档来源所有者和发布属性指纹。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0036"
down_revision: str | None = "20260824_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """让来源身份和正文 checksum 分离，支持安全的元数据原子更新。"""

    op.add_column("knowledge_documents", sa.Column("owner_user_id", sa.Text(), nullable=True))
    op.add_column(
        "knowledge_documents",
        sa.Column("publication_fingerprint", sa.Text(), nullable=True),
    )
    # 旧版本只在 Chunk 保存 PRIVATE 所有者。若一个文档的所有 Chunk 都指向同一
    # 所有者，则可以无歧义回填主表；异常的多所有者数据保持 NULL，宁可后续重新审核，
    # 也不在迁移阶段擅自扩大其可见范围。
    op.execute(
        """
        UPDATE knowledge_documents AS d
        SET owner_user_id = owners.owner_user_id
        FROM (
            SELECT document_id, MIN(owner_user_id) AS owner_user_id
            FROM knowledge_chunks
            WHERE owner_user_id IS NOT NULL
            GROUP BY document_id
            HAVING COUNT(DISTINCT owner_user_id) = 1
        ) AS owners
        WHERE d.id = owners.document_id
          AND d.visibility = 'PRIVATE'
          AND d.owner_user_id IS NULL
        """
    )
    # 原有唯一约束只包含 source_uri/version，会把不同机构的同名来源错误地视为冲突。
    # 用 COALESCE 将 NULL 作用域纳入唯一键：GLOBAL 的机构和所有者均为空时仍严格唯一。
    op.drop_constraint(
        "uq_knowledge_documents_source_version",
        "knowledge_documents",
        type_="unique",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_knowledge_documents_source_scope_version
        ON knowledge_documents (
            source_uri,
            visibility,
            COALESCE(organization_id, ''),
            COALESCE(owner_user_id, ''),
            version
        )
        """
    )
    op.create_index(
        "ix_knowledge_documents_source_scope_status",
        "knowledge_documents",
        ["source_uri", "visibility", "organization_id", "owner_user_id", "status", "version"],
    )


def downgrade() -> None:
    """回退前要求数据已恢复到旧的全局 source_uri/version 唯一语义。"""

    op.drop_index(
        "ix_knowledge_documents_source_scope_status",
        table_name="knowledge_documents",
    )
    op.execute("DROP INDEX uq_knowledge_documents_source_scope_version")
    op.create_unique_constraint(
        "uq_knowledge_documents_source_version",
        "knowledge_documents",
        ["source_uri", "version"],
    )
    op.drop_column("knowledge_documents", "publication_fingerprint")
    op.drop_column("knowledge_documents", "owner_user_id")
