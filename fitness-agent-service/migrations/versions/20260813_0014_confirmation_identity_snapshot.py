"""为确认单绑定创建时的角色和机构范围快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0014"
down_revision: str | None = "20260813_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存确认创建时的最小授权快照，等待期间身份变化时拒绝继续。"""

    op.add_column(
        "agent_action_confirmations",
        sa.Column(
            "actor_roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="创建确认时的角色快照",
        ),
    )
    op.add_column(
        "agent_action_confirmations",
        sa.Column(
            "actor_organization_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="创建确认时的完整机构范围快照",
        ),
    )


def downgrade() -> None:
    """回滚授权快照字段。"""

    op.drop_column("agent_action_confirmations", "actor_organization_ids")
    op.drop_column("agent_action_confirmations", "actor_roles")
