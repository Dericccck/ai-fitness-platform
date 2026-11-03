"""Track safe cleanup of expired upload staging objects."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0054"
down_revision: str | None = "20261103_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_media_uploads", sa.Column("staging_cleared_at", sa.DateTime(timezone=True))
    )
    op.add_column("agent_media_uploads", sa.Column("staging_cleanup_token", sa.Text()))
    op.add_column(
        "agent_media_uploads", sa.Column("staging_cleanup_deadline", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_media_staging_cleanup_lease",
        "agent_media_uploads",
        "(staging_cleanup_token IS NULL) = (staging_cleanup_deadline IS NULL)",
    )
    op.create_index(
        "ix_media_staging_cleanup",
        "agent_media_uploads",
        ["expires_at", "upload_id"],
        postgresql_where=sa.text("staging_cleared_at IS NULL AND status = 'READY'"),
    )
    op.create_index(
        "ix_media_asset_expiry_active",
        "agent_media_assets",
        ["expires_at", "asset_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_media_asset_expiry_active", table_name="agent_media_assets")
    op.drop_index("ix_media_staging_cleanup", table_name="agent_media_uploads")
    op.drop_constraint("ck_media_staging_cleanup_lease", "agent_media_uploads", type_="check")
    op.drop_column("agent_media_uploads", "staging_cleanup_deadline")
    op.drop_column("agent_media_uploads", "staging_cleanup_token")
    op.drop_column("agent_media_uploads", "staging_cleared_at")
