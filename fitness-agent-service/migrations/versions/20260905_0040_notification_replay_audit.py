"""增加通知 DEAD 重放的处理人和原因审计字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0040"
down_revision: str | None = "20260905_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_notification_outbox", sa.Column("last_replayed_by", sa.Text(), nullable=True))
    op.add_column("agent_notification_outbox", sa.Column("last_replay_reason", sa.Text(), nullable=True))
    op.add_column("agent_notification_outbox", sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_notification_outbox", "replayed_at")
    op.drop_column("agent_notification_outbox", "last_replay_reason")
    op.drop_column("agent_notification_outbox", "last_replayed_by")
