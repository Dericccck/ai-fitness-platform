"""Fence multimodal prompt history against revoked personalization."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0055"
down_revision: str | None = "20261103_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_chat_runs", sa.Column("multimodal_memory_revision", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("agent_chat_runs", "multimodal_memory_revision")
