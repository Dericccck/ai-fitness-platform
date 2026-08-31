"""增加 Operations 营收金额固定指标的审计约束。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0032"
down_revision: str | None = "20260817_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """允许审计保存营收金额查询，同时继续拒绝任意指标 ID。"""

    op.drop_constraint(
        "ck_agent_operations_audit_metric",
        "agent_operations_query_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_operations_audit_metric",
        "agent_operations_query_audits",
        "metric IN ('APPOINTMENT_COUNT', 'APPOINTMENT_STATUS_BREAKDOWN', "
        "'COMPLETED_CLASS_COUNT', 'NEW_CUSTOMER_COUNT', 'REVENUE_AMOUNT', "
        "'COURSE_APPOINTMENT_COUNT', 'COACH_APPOINTMENT_COUNT', "
        "'REMAINING_CLASS_HOURS')",
    )
    op.alter_column(
        "agent_operations_query_audits",
        "metric",
        comment="固定指标 ID：预约、预约状态、完课量、新客量、营收、课程预约、教练预约或课程剩余课时，不允许任意 SQL 指标",
    )


def downgrade() -> None:
    """存在营收审计记录时拒绝回退，避免静默删除审计事实。"""

    connection = op.get_bind()
    revenue_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM agent_operations_query_audits WHERE metric = 'REVENUE_AMOUNT'"
        )
    ).scalar_one()
    if revenue_count:
        raise RuntimeError("无法回退营收金额指标迁移，因为仍存在营收金额审计记录")
    op.drop_constraint(
        "ck_agent_operations_audit_metric",
        "agent_operations_query_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_operations_audit_metric",
        "agent_operations_query_audits",
        "metric IN ('APPOINTMENT_COUNT', 'APPOINTMENT_STATUS_BREAKDOWN', "
        "'COMPLETED_CLASS_COUNT', 'NEW_CUSTOMER_COUNT', "
        "'COURSE_APPOINTMENT_COUNT', 'COACH_APPOINTMENT_COUNT', "
        "'REMAINING_CLASS_HOURS')",
    )
    op.alter_column(
        "agent_operations_query_audits",
        "metric",
        comment="固定指标 ID：预约、预约状态、完课量、新客量、课程预约、教练预约或课程剩余课时，不允许任意 SQL 指标",
    )
