"""增加经营 Agent 经营查询的持久化审计表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0028"
down_revision: str | None = "20260815_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存管理员固定指标查询的最小审计事实，不保存 SQL 或业务明细。"""

    op.create_table(
        "agent_operations_query_audits",
        sa.Column("id", sa.Text(), primary_key=True, comment="查询审计事件唯一标识"),
        sa.Column(
            "subject_user_id",
            sa.Text(),
            nullable=True,
            comment="签名 AgentContext 的主体；系统任务可以为空",
        ),
        sa.Column(
            "actor_roles",
            sa.Text(),
            nullable=True,
            comment="执行时的角色快照，逗号分隔；不作为权限判断依据",
        ),
        sa.Column("organization_id", sa.Text(), nullable=False, comment="查询所属机构"),
        sa.Column(
            "metric",
            sa.Text(),
            nullable=False,
            comment="固定指标 ID，不允许任意 SQL 指标",
        ),
        sa.Column("bucket", sa.Text(), nullable=False, comment="时间桶：NONE、DAY 或 WEEK"),
        sa.Column(
            "comparison_role",
            sa.Text(),
            nullable=False,
            comment="比较角色：CURRENT 当前周期或 PREVIOUS_PERIOD 上一等长周期",
        ),
        sa.Column("from_date", sa.Date(), nullable=True, comment="查询起始日期；失败前可能为空"),
        sa.Column("to_date", sa.Date(), nullable=True, comment="查询结束日期；失败前可能为空"),
        sa.Column(
            "row_count",
            sa.Integer(),
            nullable=True,
            comment="Gateway 返回的聚合行数；失败查询为空",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="状态：SUCCEEDED 成功或 FAILED 失败",
        ),
        sa.Column(
            "error_code",
            sa.Text(),
            nullable=True,
            comment="受控失败编码，仅保存异常类型名，不保存异常正文",
        ),
        sa.Column("request_id", sa.Text(), nullable=True, comment="跨服务请求 ID"),
        sa.Column("trace_id", sa.Text(), nullable=True, comment="跨服务链路追踪 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="审计事件写入时间",
        ),
        sa.CheckConstraint(
            "length(organization_id) BETWEEN 1 AND 200",
            name="ck_agent_operations_audit_organization",
        ),
        sa.CheckConstraint(
            "metric IN ('APPOINTMENT_COUNT', 'APPOINTMENT_STATUS_BREAKDOWN', "
            "'COURSE_APPOINTMENT_COUNT', 'COACH_APPOINTMENT_COUNT', 'REMAINING_CLASS_HOURS')",
            name="ck_agent_operations_audit_metric",
        ),
        sa.CheckConstraint(
            "bucket IN ('NONE', 'DAY', 'WEEK')",
            name="ck_agent_operations_audit_bucket",
        ),
        sa.CheckConstraint(
            "comparison_role IN ('CURRENT', 'PREVIOUS_PERIOD')",
            name="ck_agent_operations_audit_comparison_role",
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0", name="ck_agent_operations_audit_rows"
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED')",
            name="ck_agent_operations_audit_status",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND error_code IS NULL) OR "
            "(status = 'FAILED' AND error_code IS NOT NULL)",
            name="ck_agent_operations_audit_error_consistency",
        ),
    )
    op.create_index(
        "ix_agent_operations_audits_organization_created",
        "agent_operations_query_audits",
        ["organization_id", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_operations_audits_subject_created",
        "agent_operations_query_audits",
        ["subject_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_operations_audits_request_created",
        "agent_operations_query_audits",
        ["request_id", "created_at", "id"],
    )
    op.create_table_comment(
        "agent_operations_query_audits",
        "管理员经营指标查询审计；只保存主体、机构、固定指标、范围和结果规模，不保存 SQL、Prompt、明细或模型输出",
    )


def downgrade() -> None:
    """删除 Operations 查询审计表。"""

    op.drop_index(
        "ix_agent_operations_audits_request_created",
        table_name="agent_operations_query_audits",
    )
    op.drop_index(
        "ix_agent_operations_audits_subject_created",
        table_name="agent_operations_query_audits",
    )
    op.drop_index(
        "ix_agent_operations_audits_organization_created",
        table_name="agent_operations_query_audits",
    )
    op.drop_table("agent_operations_query_audits")
