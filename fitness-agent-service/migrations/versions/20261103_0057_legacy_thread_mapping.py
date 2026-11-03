"""Map an authorized legacy checkpoint thread to a mobile conversation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0057"
down_revision: str | None = "20261103_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_chat_conversations", sa.Column("legacy_thread_id", sa.Text()))
    op.create_index(
        "ix_chat_history_owner_subject_updated",
        "agent_chat_history_commits",
        ["owner_hash", "subject_user_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_conversation_legacy_owner",
        "agent_chat_conversations",
        ["owner_hash", "legacy_thread_id"],
        unique=True,
        postgresql_where=sa.text("legacy_thread_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_legacy_owner", table_name="agent_chat_conversations")
    op.drop_index("ix_chat_history_owner_subject_updated", table_name="agent_chat_history_commits")
    op.drop_column("agent_chat_conversations", "legacy_thread_id")
