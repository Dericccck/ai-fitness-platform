"""固定确认执行通道/契约版本，历史可重试失败转为仅对账的 UNKNOWN。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0048"
down_revision: str | None = "20261007_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0039 扩充了执行状态，但遗漏事件种类；真实 UNKNOWN 写入必须先修复该约束。
    op.drop_constraint(
        "ck_agent_confirmation_event_type", "agent_action_confirmation_events", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_confirmation_event_type",
        "agent_action_confirmation_events",
        "event_type IN ('CREATED', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED', "
        "'ISSUED', 'CLAIMED', 'CONSUMED', 'REQUEUED', 'EXECUTION_SUCCEEDED', "
        "'EXECUTION_FAILED', 'EXECUTION_UNKNOWN')",
    )
    op.add_column(
        "agent_action_confirmations",
        sa.Column("transport", sa.Text(), nullable=False, server_default="http"),
    )
    op.add_column(
        "agent_action_confirmations",
        sa.Column("contract_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.create_check_constraint(
        "ck_confirmation_execution_transport",
        "agent_action_confirmations",
        "transport IN ('http', 'mcp', 'local')",
    )
    # Memory 从来不经过 Java HTTP，旧动作也必须继续交给本地执行器。
    op.execute(
        "UPDATE agent_action_confirmations SET transport='local' "
        "WHERE tool_id IN ('fitness.memory.save.v1', 'fitness.memory.revoke.v1')"
    )
    op.execute("""
        WITH changed AS (
            UPDATE agent_action_confirmations
            SET execution_status='UNKNOWN', version=version+1,
                finished_at=CURRENT_TIMESTAMP,
                last_error_code='LEGACY_EXECUTION_RESULT_UNKNOWN'
            WHERE execution_status='FAILED_RETRYABLE'
            RETURNING *
        )
        INSERT INTO agent_action_confirmation_events (
            confirmation_id, event_type, actor_user_id, request_id, decision_request_id,
            trace_id, authorization_status, execution_status, authorization_version,
            actor_roles, actor_organization_ids
        )
        SELECT id, 'EXECUTION_UNKNOWN', NULL, request_id, decision_request_id,
            'confirmation-contract-migration', authorization_status, execution_status,
            version, actor_roles, actor_organization_ids FROM changed
    """)


def downgrade() -> None:
    # UNKNOWN 的转换有真实审计意义，降级不能恢复成可重放的 FAILED_RETRYABLE。
    # 在途 MCP 动作不能丢失固定路由；必须先完成或由运维处置，禁止静默改走 HTTP。
    # EXECUTION_UNKNOWN 事件约束属于旧版本缺陷修复，保留它和不可变审计记录。
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM agent_action_confirmations
                WHERE transport='mcp'
                  AND authorization_status IN ('PENDING','APPROVED')
                  AND execution_status IN ('NOT_STARTED','RUNNING','UNKNOWN','FAILED_RETRYABLE')
            ) THEN
                RAISE EXCEPTION 'Unresolved MCP confirmations prevent contract downgrade';
            END IF;
        END $$
    """)
    op.drop_constraint(
        "ck_confirmation_execution_transport", "agent_action_confirmations", type_="check"
    )
    op.drop_column("agent_action_confirmations", "contract_version")
    op.drop_column("agent_action_confirmations", "transport")
