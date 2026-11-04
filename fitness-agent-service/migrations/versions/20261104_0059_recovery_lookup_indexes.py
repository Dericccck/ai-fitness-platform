"""Index personal conversation recovery and its ownership consistency checks.

These ordinary transactional indexes take a write-conflicting table lock while
building; schedule this migration in a maintenance window on large installations.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0059"
down_revision: str | None = "20261103_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_conversation_recovery_scope",
        "agent_chat_conversations",
        [
            "subject_user_id",
            "organization_scope",
            sa.text("updated_at DESC"),
            sa.text("conversation_id DESC"),
        ],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_media_asset_conversation", "agent_media_assets", ["conversation_id", "asset_id"]
    )
    op.create_index(
        "ix_movement_report_conversation",
        "agent_movement_reports",
        ["conversation_id", "report_id"],
    )
    op.create_index(
        "ix_chat_request_conversation",
        "agent_chat_request_keys",
        ["conversation_id", "client_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_request_conversation", table_name="agent_chat_request_keys")
    op.drop_index("ix_movement_report_conversation", table_name="agent_movement_reports")
    op.drop_index("ix_media_asset_conversation", table_name="agent_media_assets")
    op.drop_index("ix_conversation_recovery_scope", table_name="agent_chat_conversations")
