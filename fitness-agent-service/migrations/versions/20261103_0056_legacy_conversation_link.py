"""Explicit authorization-scoped link to an existing text conversation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0056"
down_revision: str | None = "20261103_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_chat_conversations",
        sa.Column("legacy_linked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("agent_chat_conversations", "legacy_linked")
