"""受控记忆维度、主体修订号及事务锁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0050"
down_revision: str | None = "20261028_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_memory_events", sa.Column("request_fingerprint", sa.Text(), nullable=True))
    op.create_table(
        "agent_memory_profiles",
        sa.Column("subject_user_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("write_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("extraction_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "personalization_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    for name in ("slot_code", "scope_key", "item_key"):
        op.add_column("agent_memories", sa.Column(name, sa.Text(), nullable=True))
    op.create_index(
        "uq_agent_memories_active_slot_item",
        "agent_memories",
        ["subject_user_id", "organization_id", "slot_code", "scope_key", "item_key"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'ACTIVE' AND NOT content_redacted AND slot_code IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_memories_active_slot_item", table_name="agent_memories")
    for name in ("item_key", "scope_key", "slot_code"):
        op.drop_column("agent_memories", name)
    op.drop_table("agent_memory_profiles")
    op.drop_column("agent_memory_events", "request_fingerprint")
