"""Persist one-shot server-mediated media transfer attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0058"
down_revision: str | None = "20261103_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_media_gateway_attempts",
        sa.Column(
            "upload_id", sa.Text(), sa.ForeignKey("agent_media_uploads.upload_id"), primary_key=True
        ),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_media_gateway_attempts")
