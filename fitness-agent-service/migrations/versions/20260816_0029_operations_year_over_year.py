"""为 Operations 审计增加正式同比比较角色。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0029"
down_revision: str | None = "20260815_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """允许记录上一自然年同期查询结果，保留原有环比数据兼容性。"""

    op.drop_constraint(
        "ck_agent_operations_audit_comparison_role",
        "agent_operations_query_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_operations_audit_comparison_role",
        "agent_operations_query_audits",
        "comparison_role IN ('CURRENT', 'PREVIOUS_PERIOD', 'SAME_PERIOD_LAST_YEAR')",
    )
    op.alter_column(
        "agent_operations_query_audits",
        "comparison_role",
        comment="比较角色：CURRENT 当前周期、PREVIOUS_PERIOD 上一等长周期或 SAME_PERIOD_LAST_YEAR 上一自然年同期",
    )


def downgrade() -> None:
    """存在同比审计记录时拒绝回退，避免为恢复旧约束而静默删除审计事实。"""

    connection = op.get_bind()
    yoy_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM agent_operations_query_audits "
            "WHERE comparison_role = 'SAME_PERIOD_LAST_YEAR'"
        )
    ).scalar_one()
    if yoy_count:
        raise RuntimeError("无法回退经营同比迁移，因为仍存在同比审计记录")
    op.drop_constraint(
        "ck_agent_operations_audit_comparison_role",
        "agent_operations_query_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_operations_audit_comparison_role",
        "agent_operations_query_audits",
        "comparison_role IN ('CURRENT', 'PREVIOUS_PERIOD')",
    )
    op.alter_column(
        "agent_operations_query_audits",
        "comparison_role",
        comment="比较角色：CURRENT 当前周期或 PREVIOUS_PERIOD 上一等长周期",
    )
