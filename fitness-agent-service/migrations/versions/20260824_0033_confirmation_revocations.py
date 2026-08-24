"""补齐确认单主动撤销的幂等字段和时间字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0033"
down_revision: str | None = "20260817_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存主动撤销事实；空值允许历史确认单继续使用。"""

    op.add_column(
        "agent_action_confirmations",
        sa.Column(
            "cancelled_at", sa.DateTime(timezone=True), nullable=True, comment="主动撤销时间"
        ),
    )
    op.add_column(
        "agent_action_confirmations",
        sa.Column(
            "revocation_request_id",
            sa.Text(),
            nullable=True,
            comment="主动撤销请求幂等标识",
        ),
    )
    op.create_unique_constraint(
        "uq_agent_confirmation_revocation_request",
        "agent_action_confirmations",
        ["revocation_request_id"],
    )


def downgrade() -> None:
    """回退前必须确认没有撤销事实，避免删除审计关联字段。"""

    connection = op.get_bind()
    revoked_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM agent_action_confirmations "
            "WHERE revocation_request_id IS NOT NULL"
        )
    ).scalar_one()
    if revoked_count:
        raise RuntimeError("cannot downgrade confirmation revocation migration while facts exist")
    op.drop_constraint(
        "uq_agent_confirmation_revocation_request",
        "agent_action_confirmations",
        type_="unique",
    )
    op.drop_column("agent_action_confirmations", "revocation_request_id")
    op.drop_column("agent_action_confirmations", "cancelled_at")
