"""私有媒体、消息投影及多模态持久调度的基础表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0053"
down_revision: str | None = "20261029_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def _updated() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    op.drop_index("ix_chat_run_active_scope", table_name="agent_chat_runs")
    op.drop_constraint("ck_chat_run_status", "agent_chat_runs", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "agent_chat_runs",
        "status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','CANCELLED','COMPLETED','FAILED','UNKNOWN')",
    )
    op.create_index(
        "ix_chat_run_active_scope",
        "agent_chat_runs",
        ["conversation_scope_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')"),
    )
    op.add_column(
        "agent_chat_runs",
        sa.Column("executor_kind", sa.Text(), nullable=False, server_default="LEGACY"),
    )
    op.add_column("agent_chat_runs", sa.Column("stage", sa.Text()))
    op.add_column(
        "agent_chat_runs",
        sa.Column("stage_version", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("agent_chat_runs", sa.Column("queued_at", sa.DateTime(timezone=True)))
    op.add_column("agent_chat_runs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column(
        "agent_chat_runs",
        sa.Column("content_epoch", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("agent_chat_runs", sa.Column("retry_of", sa.Text()))
    op.create_check_constraint(
        "ck_chat_run_executor", "agent_chat_runs", "executor_kind IN ('LEGACY','MULTIMODAL')"
    )

    op.create_table(
        "agent_chat_conversations",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_scope", sa.Text(), nullable=False),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("title_ciphertext", sa.LargeBinary()),
        sa.Column("key_version", sa.Text()),
        sa.Column("content_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _created(),
        _updated(),
        sa.CheckConstraint(
            "(title_ciphertext IS NULL) = (key_version IS NULL)",
            name="ck_conversation_title_cipher",
        ),
    )
    op.create_index(
        "ix_conversation_owner", "agent_chat_conversations", ["owner_hash", "updated_at"]
    )

    op.create_table(
        "agent_media_assets",
        sa.Column("asset_id", sa.Text(), primary_key=True),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("organization_scope", sa.Text(), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_chat_conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("final_key", sa.Text()),
        sa.Column("final_version", sa.Text()),
        sa.Column("sha256", sa.Text()),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _created(),
        _updated(),
        sa.CheckConstraint("kind IN ('image','video')", name="ck_media_asset_kind"),
        sa.CheckConstraint(
            "status IN ('UPLOADING','VERIFYING','READY','REJECTED','DELETED','EXPIRED')",
            name="ck_media_asset_status",
        ),
        sa.CheckConstraint(
            "revision > 0 AND (size_bytes IS NULL OR size_bytes > 0)", name="ck_media_asset_bounds"
        ),
        sa.CheckConstraint(
            "status != 'READY' OR (final_key IS NOT NULL AND final_version IS NOT NULL "
            "AND sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name="ck_media_asset_ready",
        ),
    )
    op.create_index("ix_media_asset_retention", "agent_media_assets", ["status", "expires_at"])
    op.create_index("ix_media_asset_owner", "agent_media_assets", ["owner_hash", "conversation_id"])

    op.create_table(
        "agent_media_uploads",
        sa.Column("upload_id", sa.Text(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Text(),
            sa.ForeignKey("agent_media_assets.asset_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("staging_key", sa.Text(), nullable=False, unique=True),
        sa.Column("source_version", sa.Text()),
        sa.Column("verification_token", sa.Text()),
        sa.Column("verification_deadline", sa.DateTime(timezone=True)),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        _updated(),
        sa.UniqueConstraint("owner_hash", "idempotency_key", name="uq_media_upload_idempotency"),
        sa.CheckConstraint("max_bytes > 0", name="ck_media_upload_max_bytes"),
        sa.CheckConstraint(
            "(verification_token IS NULL) = (verification_deadline IS NULL)",
            name="ck_media_upload_verification_lease",
        ),
        sa.CheckConstraint(
            "status IN ('ISSUED','UPLOADED','VERIFYING','READY','REJECTED','EXPIRED','CANCELLED')",
            name="ck_media_upload_status",
        ),
    )
    op.create_index("ix_media_upload_retention", "agent_media_uploads", ["status", "expires_at"])

    op.create_table(
        "agent_chat_messages",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_chat_conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text()),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_ciphertext", sa.LargeBinary()),
        sa.Column("key_version", sa.Text()),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _created(),
        _updated(),
        sa.UniqueConstraint("run_id", "role", name="uq_chat_message_run_role"),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_chat_message_role"),
        sa.CheckConstraint(
            "(content_ciphertext IS NULL) = (key_version IS NULL)", name="ck_chat_message_cipher"
        ),
    )
    op.create_index(
        "ix_chat_message_page",
        "agent_chat_messages",
        ["conversation_id", "created_at", "message_id"],
    )
    op.create_table(
        "agent_chat_message_media",
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey("agent_chat_messages.message_id"),
            primary_key=True,
        ),
        sa.Column(
            "asset_id", sa.Text(), sa.ForeignKey("agent_media_assets.asset_id"), primary_key=True
        ),
        sa.Column("asset_revision", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("dependency_type", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "asset_revision > 0 AND ordinal >= 0", name="ck_chat_message_media_bounds"
        ),
    )
    op.create_index("ix_chat_message_media_asset", "agent_chat_message_media", ["asset_id"])

    op.create_table(
        "agent_movement_reports",
        sa.Column("report_id", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False, unique=True),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_chat_conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("report_ciphertext", sa.LargeBinary()),
        sa.Column("key_version", sa.Text()),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("media_manifest_hash", sa.Text(), nullable=False),
        sa.Column("content_epoch", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _created(),
        sa.CheckConstraint(
            "(report_ciphertext IS NULL) = (key_version IS NULL)", name="ck_movement_report_cipher"
        ),
    )
    op.create_table(
        "agent_chat_request_keys",
        sa.Column("owner_hash", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("client_message_id", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False, unique=True),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("content_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
    )
    op.create_index("ix_chat_request_key_expiry", "agent_chat_request_keys", ["expires_at"])

    op.create_table(
        "agent_chat_run_dispatch",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("agent_chat_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Text(), nullable=False),
        sa.Column("auth_session_ref", sa.Text(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_token", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "provider_dispatch_started", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        _created(),
        _updated(),
        sa.CheckConstraint("attempt >= 0", name="ck_chat_dispatch_attempt"),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)", name="ck_chat_dispatch_lease"
        ),
    )
    op.create_index(
        "ix_chat_dispatch_available", "agent_chat_run_dispatch", ["available_at", "lease_until"]
    )
    op.create_table(
        "agent_media_deletions",
        sa.Column("deletion_id", sa.Text(), primary_key=True),
        sa.Column("owner_hash", sa.Text(), nullable=False),
        sa.Column(
            "asset_id", sa.Text(), sa.ForeignKey("agent_media_assets.asset_id"), nullable=False
        ),
        sa.Column("request_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("cleared_once", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _created(),
        _updated(),
        sa.UniqueConstraint("asset_id", name="uq_media_deletion_asset"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED')", name="ck_media_deletion_status"
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)", name="ck_media_deletion_lease"
        ),
    )
    op.create_index("ix_media_deletion_retry", "agent_media_deletions", ["status", "retry_at"])


def downgrade() -> None:
    # 必须先关闭多模态、排空worker并保留业务数据备份，再允许回滚。
    op.execute(
        "UPDATE agent_chat_runs SET status='UNKNOWN', error_code='EXECUTION_INTERRUPTED' WHERE status='QUEUED'"
    )
    for table in (
        "agent_media_deletions",
        "agent_chat_run_dispatch",
        "agent_chat_request_keys",
        "agent_movement_reports",
        "agent_chat_message_media",
        "agent_chat_messages",
        "agent_media_uploads",
        "agent_media_assets",
        "agent_chat_conversations",
    ):
        op.drop_table(table)
    op.drop_constraint("ck_chat_run_executor", "agent_chat_runs", type_="check")
    for name in (
        "retry_of",
        "content_epoch",
        "started_at",
        "queued_at",
        "stage_version",
        "stage",
        "executor_kind",
    ):
        op.drop_column("agent_chat_runs", name)
    op.drop_index("ix_chat_run_active_scope", table_name="agent_chat_runs")
    op.drop_constraint("ck_chat_run_status", "agent_chat_runs", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "agent_chat_runs",
        "status IN ('RUNNING','CANCEL_REQUESTED','CANCELLED','COMPLETED','FAILED','UNKNOWN')",
    )
    op.create_index(
        "ix_chat_run_active_scope",
        "agent_chat_runs",
        ["conversation_scope_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('RUNNING','CANCEL_REQUESTED')"),
    )
