"""扩展站内通知收件箱，允许训练计划主动提醒类型。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0035"
down_revision: str | None = "20260824_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOTIFICATION_TYPES = (
    "'MEMORY_CANDIDATE_PENDING'",
    "'APPOINTMENT_CREATED'",
    "'APPOINTMENT_RESCHEDULED'",
    "'APPOINTMENT_CANCELLED'",
    "'TRAINING_PLAN_PUBLISHED'",
    "'TRAINING_PLAN_REVIEW_REQUIRED'",
)


def upgrade() -> None:
    """让收件箱约束与主动提醒事件契约保持一致。

    0034 已经扩展了 Outbox 和用户偏好表，但站内收件箱表仍沿用了早期只支持
    Memory 的约束。PostgreSQL 会在 Worker 投递阶段拒绝训练通知，因此这里使用
    独立迁移补齐收件箱约束，兼容已经执行过 0034 的本地和生产数据库。
    """

    notification_types = ", ".join(_NOTIFICATION_TYPES)
    op.drop_constraint(
        "ck_agent_in_app_notification_type",
        "agent_in_app_notifications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_in_app_notification_type",
        "agent_in_app_notifications",
        f"notification_type IN ({notification_types})",
    )


def downgrade() -> None:
    """仅当没有非 Memory 站内通知时才允许回退，避免删除已有业务通知。"""

    connection = op.get_bind()
    non_memory_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM agent_in_app_notifications
            WHERE notification_type <> 'MEMORY_CANDIDATE_PENDING'
            """
        )
    ).scalar_one()
    if non_memory_count:
        raise RuntimeError(
            "cannot downgrade training notification types while non-memory notifications exist"
        )
    op.drop_constraint(
        "ck_agent_in_app_notification_type",
        "agent_in_app_notifications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_in_app_notification_type",
        "agent_in_app_notifications",
        "notification_type IN ('MEMORY_CANDIDATE_PENDING')",
    )
