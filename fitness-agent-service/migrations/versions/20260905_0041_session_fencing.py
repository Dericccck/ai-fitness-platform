"""增加会话 Checkpoint fencing 代次表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0041"
down_revision: str | None = "20260905_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_session_fences",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("fencing_token > 0", name="ck_agent_session_fence_positive"),
    )


def downgrade() -> None:
    op.drop_table("agent_session_fences")
