"""为主动事件保存契约版本、聚合版本和发生时间。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0038"
down_revision: str | None = "20260905_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_proactive_event_inbox", sa.Column("contract_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("agent_proactive_event_inbox", sa.Column("aggregate_version", sa.Integer(), nullable=True))
    op.add_column("agent_proactive_event_inbox", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_proactive_event_inbox", "occurred_at")
    op.drop_column("agent_proactive_event_inbox", "aggregate_version")
    op.drop_column("agent_proactive_event_inbox", "contract_version")
