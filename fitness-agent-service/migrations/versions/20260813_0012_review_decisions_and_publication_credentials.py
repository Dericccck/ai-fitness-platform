"""新增专业审核决定和知识发布凭证。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存签名审核身份快照、精确审核范围和 Worker 可验证发布凭证。"""

    op.create_table(
        "knowledge_review_decisions",
        sa.Column("id", sa.Text(), primary_key=True, comment="专业审核决定唯一标识"),
        sa.Column(
            "report_id",
            sa.Text(),
            sa.ForeignKey("knowledge_review_reports.id", ondelete="CASCADE"),
            nullable=False,
            comment="本决定绑定的不可变审核报告标识",
        ),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
            comment="本决定所属知识上传任务标识",
        ),
        sa.Column("review_domain", sa.Text(), nullable=False, comment="专业审核领域"),
        sa.Column("decision", sa.Text(), nullable=False, comment="审核决定：APPROVED 或 REJECTED"),
        sa.Column("scope_type", sa.Text(), nullable=False, comment="审核范围：DOCUMENT 或 PAGES"),
        sa.Column(
            "page_numbers",
            sa.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
            comment="页级审核覆盖的已排序 PDF 页码；文档级审核为空",
        ),
        sa.Column(
            "regions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
            comment="页内归一化矩形区域、标签等视觉审核证据",
        ),
        sa.Column(
            "finding_codes",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="本决定处理的审核报告结论编码",
        ),
        sa.Column("reviewer_id", sa.Text(), nullable=False, comment="签名上下文中的审核人主体标识"),
        sa.Column(
            "reviewer_roles", sa.ARRAY(sa.Text()), nullable=False, comment="决定时刻的签名角色快照"
        ),
        sa.Column(
            "reviewer_capabilities",
            sa.ARRAY(sa.Text()),
            nullable=False,
            comment="决定时刻的签名知识审核能力快照",
        ),
        sa.Column(
            "reviewer_qualifications",
            sa.ARRAY(sa.Text()),
            nullable=False,
            comment="决定时刻的已核验专业资质快照",
        ),
        sa.Column(
            "reviewer_organization_ids",
            sa.ARRAY(sa.Text()),
            nullable=False,
            comment="决定时刻的签名组织范围快照",
        ),
        sa.Column("comment", sa.Text(), nullable=False, comment="审核依据、风险说明或拒绝原因"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="审核决定创建时间",
        ),
        sa.CheckConstraint(
            "review_domain IN ('FITNESS_COACHING_SAFETY', 'FITNESS_CONTENT_REVIEW', "
            "'CLINICAL_EXERCISE_SAFETY')",
            name="ck_knowledge_review_decisions_domain",
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')",
            name="ck_knowledge_review_decisions_value",
        ),
        sa.CheckConstraint(
            "scope_type IN ('DOCUMENT', 'PAGES')",
            name="ck_knowledge_review_decisions_scope",
        ),
        sa.CheckConstraint(
            "(scope_type = 'DOCUMENT' AND cardinality(page_numbers) = 0) OR "
            "(scope_type = 'PAGES' AND cardinality(page_numbers) > 0)",
            name="ck_knowledge_review_decisions_pages",
        ),
        sa.UniqueConstraint(
            "report_id", "review_domain", name="uq_knowledge_review_decisions_report_domain"
        ),
        comment="知识版本的追加式专业审核决定与签名授权快照表",
    )
    op.create_index(
        "ix_knowledge_review_decisions_job",
        "knowledge_review_decisions",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_knowledge_review_decisions_reviewer",
        "knowledge_review_decisions",
        ["reviewer_id", "created_at"],
    )

    op.create_table(
        "knowledge_publication_credentials",
        sa.Column("id", sa.Text(), primary_key=True, comment="知识发布凭证唯一标识"),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            comment="凭证绑定的知识上传任务标识",
        ),
        sa.Column(
            "report_id",
            sa.Text(),
            sa.ForeignKey("knowledge_review_reports.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            comment="凭证绑定的不可变审核报告标识",
        ),
        sa.Column("report_version", sa.Integer(), nullable=False, comment="凭证绑定的审核报告版本"),
        sa.Column(
            "document_sha256", sa.Text(), nullable=False, comment="凭证绑定的原始文件 SHA-256"
        ),
        sa.Column(
            "parser_pipeline_version", sa.Text(), nullable=False, comment="凭证绑定的解析管线版本"
        ),
        sa.Column(
            "review_policy_version", sa.Text(), nullable=False, comment="凭证绑定的审核策略版本"
        ),
        sa.Column(
            "decision_ids",
            sa.ARRAY(sa.Text()),
            nullable=False,
            comment="满足全部必需领域的专业审核决定标识",
        ),
        sa.Column(
            "approved_visual_pages",
            sa.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
            comment="允许解除纯人工视觉审核路由的 PDF 页码；不能解除 OCR 阻断",
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="全部必需领域审核完成后自动签发时间",
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, comment="凭证撤销时间"),
        sa.CheckConstraint(
            "report_version > 0", name="ck_knowledge_publication_credentials_version_positive"
        ),
        comment="Worker 发布前用于校验审核完整性、文件哈希和策略版本的数据库凭证表",
    )
    op.create_index(
        "ix_knowledge_publication_credentials_hash",
        "knowledge_publication_credentials",
        ["document_sha256", "revoked_at"],
    )

    op.add_column(
        "knowledge_reindex_items",
        sa.Column(
            "approved_visual_pages",
            sa.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
            comment="原发布凭证允许重建的纯视觉审核页快照",
        ),
    )


def downgrade() -> None:
    """移除专业审核闭环，不修改已发布知识正文。"""

    op.drop_column("knowledge_reindex_items", "approved_visual_pages")
    op.drop_index(
        "ix_knowledge_publication_credentials_hash",
        table_name="knowledge_publication_credentials",
    )
    op.drop_table("knowledge_publication_credentials")
    op.drop_index("ix_knowledge_review_decisions_reviewer", table_name="knowledge_review_decisions")
    op.drop_index("ix_knowledge_review_decisions_job", table_name="knowledge_review_decisions")
    op.drop_table("knowledge_review_decisions")
