"""新增不可变的知识解析审核报告。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0011"
down_revision: str | None = "20260812_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存上传时的解析版本、质量指标、页级画像和审核路由证据。"""

    op.create_table(
        "knowledge_review_reports",
        sa.Column("id", sa.Text(), primary_key=True, comment="审核报告唯一标识"),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属知识上传任务标识",
        ),
        sa.Column(
            "report_version", sa.Integer(), nullable=False, comment="同一任务内的审核报告版本号"
        ),
        sa.Column(
            "document_sha256", sa.Text(), nullable=False, comment="本报告绑定的原始文件 SHA-256"
        ),
        sa.Column("parser_name", sa.Text(), nullable=False, comment="主解析引擎名称"),
        sa.Column("parser_version", sa.Text(), nullable=False, comment="主解析引擎版本"),
        sa.Column(
            "parser_pipeline_version",
            sa.Text(),
            nullable=False,
            comment="清洗、页面路由和父子切分管线版本",
        ),
        sa.Column(
            "review_policy_version", sa.Text(), nullable=False, comment="健身知识审核策略版本"
        ),
        sa.Column("media_type", sa.Text(), nullable=False, comment="解析后的标准媒体类型"),
        sa.Column(
            "declared_risk_level",
            sa.Text(),
            nullable=False,
            comment="可信来源声明的风险等级：NORMAL、CAUTION 或 MEDICAL",
        ),
        sa.Column(
            "source_requires_human_review",
            sa.Boolean(),
            nullable=False,
            comment="可信来源是否明确要求人工复核",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="报告结论：PASS、REVIEW_REQUIRED 或 BLOCKED",
        ),
        sa.Column(
            "quality_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="噪声率、碎片率、重复率、父子节点和页面覆盖等质量指标",
        ),
        sa.Column(
            "page_profiles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="PDF 页级图片占比、文字密度、表格、图注和处理路由画像",
        ),
        sa.Column(
            "warnings",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="解析器产生的非阻断告警",
        ),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="带稳定编码、严重级别、说明和页码的审核结论",
        ),
        sa.Column(
            "required_review_domains",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="发布前必须完成的审核领域",
        ),
        sa.Column(
            "recommended_reviewer_roles",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="现有平台中建议承担审核的角色",
        ),
        sa.Column(
            "required_qualifications",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="现有角色模型尚未表达的专业资质要求",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="审核报告创建时间",
        ),
        sa.CheckConstraint(
            "status IN ('PASS', 'REVIEW_REQUIRED', 'BLOCKED')",
            name="ck_knowledge_review_reports_status",
        ),
        sa.CheckConstraint(
            "declared_risk_level IN ('NORMAL', 'CAUTION', 'MEDICAL')",
            name="ck_knowledge_review_reports_risk_level",
        ),
        sa.CheckConstraint(
            "report_version > 0", name="ck_knowledge_review_reports_version_positive"
        ),
        sa.UniqueConstraint(
            "job_id", "report_version", name="uq_knowledge_review_reports_job_version"
        ),
        comment="知识上传解析质量和专业审核路由的不可变版本化报告表",
    )
    op.create_index(
        "ix_knowledge_review_reports_job",
        "knowledge_review_reports",
        ["job_id", "report_version"],
    )
    op.create_index(
        "ix_knowledge_review_reports_status",
        "knowledge_review_reports",
        ["status", "created_at"],
    )


def downgrade() -> None:
    """删除审核报告，不修改上传任务和已发布知识。"""

    op.drop_index("ix_knowledge_review_reports_status", table_name="knowledge_review_reports")
    op.drop_index("ix_knowledge_review_reports_job", table_name="knowledge_review_reports")
    op.drop_table("knowledge_review_reports")
