"""创建版本化健身知识库和 pgvector 索引。

本次迁移中的表属于 Agent 服务。它们有意不作为合同、预约、训练记录或其他由 Java
应用管理的业务事实的权威来源。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260812_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建文档、切片、权限元数据和 ANN 索引。"""

    # pgvector 是基础设施依赖；部署镜像缺少该扩展时必须显式迁移失败，不能静默创建
    # 无法执行向量检索的表。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("applicable_roles", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "visibility IN ('GLOBAL', 'ORGANIZATION', 'PRIVATE')",
            name="ck_knowledge_documents_visibility",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_knowledge_documents_status",
        ),
        sa.UniqueConstraint("source_uri", "version", name="uq_knowledge_documents_source_version"),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        # HNSW 要求固定向量维度。1536 是首个生产 Embedding 模型的项目契约；修改它必须
        # 使用版本化迁移并完整重建索引，不能只修改环境变量。
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("allowed_roles", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "visibility IN ('GLOBAL', 'ORGANIZATION', 'PRIVATE')",
            name="ck_knowledge_chunks_visibility",
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunks_document_index"
        ),
    )

    # 向量索引用于加速候选召回。内容返回给 Reranker 前仍会在 WHERE 子句中执行授权过滤。
    op.execute(
        "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index(
        "ix_knowledge_chunks_scope",
        "knowledge_chunks",
        ["organization_id", "visibility", "document_type", "effective_from"],
    )
    op.create_index(
        "ix_knowledge_chunks_document",
        "knowledge_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )


def downgrade() -> None:
    """删除 Agent 知识库，同时保留共享扩展。"""

    op.drop_index("ix_knowledge_chunks_document", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_scope", table_name="knowledge_chunks")
    op.execute("DROP INDEX IF EXISTS knowledge_chunks_embedding_hnsw_idx")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
