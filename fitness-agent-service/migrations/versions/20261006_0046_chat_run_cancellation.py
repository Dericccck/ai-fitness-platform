"""显式取消流式运行及来源确认单；不存用户正文或身份令牌。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0046"
down_revision: str | None = "20261006_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_run_status", "agent_chat_runs", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "agent_chat_runs",
        "status IN ('RUNNING','CANCEL_REQUESTED','CANCELLED','COMPLETED','FAILED','UNKNOWN')",
    )
    for name in ("cancel_requested_at", "cancel_deadline", "cancelled_at"):
        op.add_column("agent_chat_runs", sa.Column(name, sa.DateTime(timezone=True)))
    for name in ("cancel_request_id", "stop_reason", "conversation_scope_hash"):
        op.add_column("agent_chat_runs", sa.Column(name, sa.Text()))
    op.create_index(
        "ix_chat_run_active_scope",
        "agent_chat_runs",
        ["conversation_scope_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('RUNNING','CANCEL_REQUESTED')"),
    )
    op.create_index(
        "ix_chat_run_cancel_deadline",
        "agent_chat_runs",
        ["cancel_deadline"],
        postgresql_where=sa.text("status='CANCEL_REQUESTED'"),
    )
    op.add_column("agent_action_confirmations", sa.Column("source_run_id", sa.Text()))
    op.create_index(
        "ix_confirmation_source_run",
        "agent_action_confirmations",
        ["source_run_id"],
        postgresql_where=sa.text("source_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 回滚前仍须 drain 执行者；未知结果不能假称业务失败。
    op.execute(
        "UPDATE agent_chat_runs SET status='UNKNOWN', error_code='EXECUTION_INTERRUPTED' "
        "WHERE status IN ('CANCEL_REQUESTED','CANCELLED')"
    )
    op.drop_index("ix_confirmation_source_run", table_name="agent_action_confirmations")
    op.drop_column("agent_action_confirmations", "source_run_id")
    op.drop_index("ix_chat_run_cancel_deadline", table_name="agent_chat_runs")
    op.drop_index("ix_chat_run_active_scope", table_name="agent_chat_runs")
    for name in (
        "cancel_requested_at",
        "cancel_deadline",
        "cancelled_at",
        "cancel_request_id",
        "stop_reason",
        "conversation_scope_hash",
    ):
        op.drop_column("agent_chat_runs", name)
    op.drop_constraint("ck_chat_run_status", "agent_chat_runs", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "agent_chat_runs",
        "status IN ('RUNNING','COMPLETED','FAILED','UNKNOWN')",
    )
