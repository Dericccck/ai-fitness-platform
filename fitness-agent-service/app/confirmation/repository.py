"""确认单 PostgreSQL 仓储。

仓储只返回确认单状态和脱敏摘要，不提供“绕过确认直接执行”的方法。批准、发放一次性
凭证标识、领取执行权和记录结果都通过带版本条件的事务完成；如果同一确认单被并发操作，
调用方得到稳定的状态冲突，而不是覆盖别人的决定。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import bindparam, text

from app.infrastructure.database import Database
from app.streaming.control import current_control

from .models import (
    AuthorizationStatus,
    ConfirmationAction,
    ConfirmationEvent,
    ConfirmationEventType,
    ConfirmationRecord,
    ConfirmationStateError,
    ExecutionStatus,
)
from .reconciliation_policy import RECONCILABLE_TOOL_IDS


class ConfirmationNotFound(ConfirmationStateError):
    """确认单不存在或不属于当前主体范围。"""


class ConfirmationRepository:
    """确认单和不可变事件的事务仓储。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(
        self, confirmation_id: str, action: ConfirmationAction, expires_at: datetime
    ) -> ConfirmationRecord:
        """创建待确认动作；同一个业务 request_id 只能成功创建一张确认单。"""

        statement = text(
            """
            INSERT INTO agent_action_confirmations (
                id, protocol_version, thread_id, subject_user_id, organization_id, tool_id,
                risk_level, action, resource_type, resource_id, expected_resource_version,
                request_id, payload_hash, display_summary, payload_ciphertext, payload_key_version,
                authorization_status, execution_status, expires_at, actor_roles,
                actor_organization_ids, source_run_id, transport, contract_version
            ) VALUES (
                :id, 1, :thread_id, :subject_user_id, :organization_id, :tool_id,
                :risk_level, :action, :resource_type, :resource_id, :expected_resource_version,
                :request_id, :payload_hash, CAST(:display_summary AS JSONB), :payload_ciphertext,
                :payload_key_version, 'PENDING', 'NOT_STARTED', :expires_at,
                :actor_roles, :actor_organization_ids, :source_run_id, :transport, :contract_version
            )
            ON CONFLICT (request_id) DO NOTHING
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            control = current_control()
            if control is not None:
                control.check()
                allowed = await connection.execute(
                    text("""
                    SELECT 1 FROM agent_chat_runs WHERE run_id=:id AND owner_hash=:owner
                    AND status='RUNNING' AND execution_deadline > CURRENT_TIMESTAMP FOR UPDATE
                """),
                    {"id": control.run_id, "owner": control.owner},
                )
                if allowed.first() is None:
                    control.cancelled = True
                    control.check()
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            **_action_params(confirmation_id, action, expires_at),
                            "source_run_id": control.run_id if control is not None else None,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                # ON CONFLICT 会把并发重复请求收敛到已存在的确认单；事务未进入失败状态，
                # 因而可以在同一个事务里安全读取并比较不可变动作摘要。
                existing = await self._select_by_request(connection, action.request_id)
                if existing is not None and _same_action(existing, action):
                    return _record_from_row(existing)
                raise ConfirmationStateError("请求 ID 已绑定到其他操作")
            record = _record_from_row(row)
            await self._insert_event(
                connection,
                _event_for(record, "CREATED", action, actor_roles=action.actor_roles),
            )
            return record

    async def cancel_for_run(self, run_id: str) -> None:
        """执行者已退出后，只撤销该运行创建且尚未执行的确认单。"""
        async with self._database.engine.begin() as connection:
            run = (
                (
                    await connection.execute(
                        text("""
                SELECT cancel_request_id FROM agent_chat_runs
                WHERE run_id=:id AND cancel_requested_at IS NOT NULL FOR UPDATE
            """),
                        {"id": run_id},
                    )
                )
                .mappings()
                .first()
            )
            if run is None:
                return
            rows = await connection.execute(
                text("""
                SELECT * FROM agent_action_confirmations WHERE source_run_id=:id
                AND authorization_status IN ('PENDING','APPROVED')
                AND execution_status='NOT_STARTED' ORDER BY id FOR UPDATE
            """),
                {"id": run_id},
            )
            now = datetime.now(UTC)
            for row in rows.mappings():
                record = _record_from_row(row)
                if record.is_expired(now):
                    changed = record.expire(now)
                    event_type: ConfirmationEventType = "EXPIRED"
                else:
                    changed = record.cancel(now, f"{run['cancel_request_id']}:{record.id}")
                    event_type = "CANCELLED"
                await self._update_record(connection, changed, expected_version=record.version)
                await self._insert_event(
                    connection,
                    _event_for(
                        changed,
                        event_type,
                        None,
                        actor_user_id=record.subject_user_id,
                        actor_roles=record.actor_roles,
                    ),
                )

    async def get_for_subject(
        self, confirmation_id: str, subject_user_id: str, organization_ids: Sequence[str]
    ) -> ConfirmationRecord:
        """只返回当前用户且属于其机构范围的确认单。"""

        statement = text(
            """
            SELECT * FROM agent_action_confirmations
            WHERE id = :id AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            FOR UPDATE
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        async with self._database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "id": confirmation_id,
                            "subject_user_id": subject_user_id,
                            "organization_ids": list(organization_ids),
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ConfirmationNotFound("未找到确认单")
            record = _record_from_row(row)
            # 页面刷新不应继续显示已经过期的待确认动作。这里使用短事务和行锁做惰性过期，
            # 仍由 ConfirmationRecord 统一判断状态转移，并记录不可变 EXPIRED 事件。
            now = datetime.now(UTC)
            if (
                record.authorization_status in {"PENDING", "APPROVED"}
                and record.execution_status == "NOT_STARTED"
                and record.is_expired(now)
            ):
                expired = record.expire(now)
                await self._update_record(connection, expired, expected_version=record.version)
                await self._insert_event(connection, _event_for(expired, "EXPIRED", None))
                return expired
            return record

    async def decide(
        self,
        confirmation_id: str,
        subject_user_id: str,
        organization_ids: Sequence[str],
        decision: str,
        decision_request_id: str,
        now: datetime,
        trace_id: str | None,
        actor_user_id: str,
        actor_roles: Sequence[str],
    ) -> ConfirmationRecord:
        """在行锁事务中批准或拒绝，并追加不可变事件。"""

        if decision not in {"APPROVE", "REJECT"}:
            raise ConfirmationStateError("决定值必须为 APPROVE 或 REJECT")
        expired_record: ConfirmationRecord | None = None
        result: ConfirmationRecord | None = None
        async with self._database.engine.begin() as connection:
            row = await self._select_for_update(
                connection, confirmation_id, subject_user_id, organization_ids
            )
            record = _record_from_row(row)
            if record.actor_roles != tuple(
                sorted(actor_roles)
            ) or record.actor_organization_ids != tuple(sorted(organization_ids)):
                raise ConfirmationStateError("确认单身份范围已变更")
            if record.authorization_status == "PENDING" and record.is_expired(now):
                expired = record.expire(now)
                await self._update_record(connection, expired, expected_version=record.version)
                await self._insert_event(
                    connection,
                    _event_for(
                        expired,
                        "EXPIRED",
                        None,
                        trace_id=trace_id,
                        actor_user_id=actor_user_id,
                        actor_roles=actor_roles,
                    ),
                )
                record = expired
                expired_record = expired
            elif record.decision_request_id == decision_request_id:
                expected_status = "APPROVED" if decision == "APPROVE" else "REJECTED"
                if record.authorization_status != expected_status:
                    raise ConfirmationStateError("decision_request_id 已被其他决定复用")
                result = record
            else:
                decided = (
                    record.approve(now, decision_request_id)
                    if decision == "APPROVE"
                    else record.reject(now, decision_request_id)
                )
                await self._update_record(connection, decided, expected_version=record.version)
                await self._insert_event(
                    connection,
                    _event_for(
                        decided,
                        "APPROVED" if decision == "APPROVE" else "REJECTED",
                        None,
                        trace_id=trace_id,
                        actor_user_id=actor_user_id,
                        actor_roles=actor_roles,
                    ),
                )
                result = decided
        if expired_record is not None:
            raise ConfirmationStateError("已过期的确认单不能作出决定")
        if result is None:
            raise AssertionError("确认决定没有产生结果")
        return result

    async def issue_credential_jti(
        self, confirmation_id: str, jti: str, now: datetime, trace_id: str | None
    ) -> ConfirmationRecord:
        """为已批准确认单绑定一次性 JTI；真正 Token 由后续签发器短暂生成。"""

        async with self._database.engine.begin() as connection:
            row = await self._select_for_update_unscoped(connection, confirmation_id)
            record = _record_from_row(row)
            if record.credential_jti == jti:
                return record
            issued = record.issue_credential(jti, now)
            await self._update_record(connection, issued, expected_version=record.version)
            await self._insert_event(
                connection, _event_for(issued, "ISSUED", None, trace_id=trace_id)
            )
            return issued

    async def cancel(
        self,
        confirmation_id: str,
        subject_user_id: str,
        organization_ids: Sequence[str],
        trace_id: str | None,
        actor_user_id: str,
        actor_roles: Sequence[str],
        revocation_request_id: str,
        now: datetime,
    ) -> ConfirmationRecord:
        """撤销尚未领取执行权的确认单，并记录操作者快照。

        撤销请求拥有独立幂等键，不能复用批准阶段的决定键。这样用户重复点击撤销
        或网络重试时会返回同一撤销事实，而不会追加第二次状态变更。
        """

        async with self._database.engine.begin() as connection:
            row = await self._select_for_update(
                connection, confirmation_id, subject_user_id, organization_ids
            )
            record = _record_from_row(row)
            if record.revocation_request_id == revocation_request_id:
                return record
            if record.authorization_status == "CANCELLED":
                raise ConfirmationStateError("确认单已经撤销")
            cancelled = record.cancel(now, revocation_request_id)
            await self._update_record(connection, cancelled, expected_version=record.version)
            await self._insert_event(
                connection,
                _event_for(
                    cancelled,
                    "CANCELLED",
                    None,
                    trace_id=trace_id,
                    actor_user_id=actor_user_id,
                    actor_roles=actor_roles,
                ),
            )
            return cancelled

    async def requeue_retryable(
        self, confirmation_id: str, trace_id: str | None
    ) -> ConfirmationRecord:
        """重新排队可恢复失败；旧 JTI 会清空并在下一步重新签发。"""

        async with self._database.engine.begin() as connection:
            row = await self._select_for_update_unscoped(connection, confirmation_id)
            record = _record_from_row(row)
            requeued = record.requeue_retryable()
            await self._update_record(connection, requeued, expected_version=record.version)
            await self._insert_event(
                connection, _event_for(requeued, "REQUEUED", None, trace_id=trace_id)
            )
            return requeued

    async def claim_execution(
        self, confirmation_id: str, now: datetime, trace_id: str | None
    ) -> ConfirmationRecord:
        """原子领取执行权；只有一个恢复请求可以从 NOT_STARTED 进入 RUNNING。"""

        async with self._database.engine.begin() as connection:
            source = (
                await connection.execute(
                    text("""
                SELECT source_run_id FROM agent_action_confirmations WHERE id=:id
            """),
                    {"id": confirmation_id},
                )
            ).scalar_one_or_none()
            if source is not None:
                ready = await connection.execute(
                    text("""
                    SELECT 1 FROM agent_chat_runs WHERE run_id=:id AND status='COMPLETED'
                    FOR SHARE
                """),
                    {"id": source},
                )
                if ready.first() is None:
                    raise ConfirmationStateError("来源对话尚未完成或已停止")
            row = await self._select_for_update_unscoped(connection, confirmation_id)
            record = _record_from_row(row)
            claimed = record.claim_execution(now)
            await self._update_record(connection, claimed, expected_version=record.version)
            await self._insert_event(
                connection, _event_for(claimed, "CLAIMED", None, trace_id=trace_id)
            )
            await self._insert_event(
                connection, _event_for(claimed, "CONSUMED", None, trace_id=trace_id)
            )
            return claimed

    async def finish_execution(
        self,
        confirmation_id: str,
        success: bool,
        now: datetime,
        trace_id: str | None,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> ConfirmationRecord:
        """记录真实业务工具的成功或失败结果。"""

        async with self._database.engine.begin() as connection:
            row = await self._select_for_update_unscoped(connection, confirmation_id)
            record = _record_from_row(row)
            if success:
                finished = record.finish_success(now)
                event_type: ConfirmationEventType = "EXECUTION_SUCCEEDED"
            else:
                if not error_code:
                    raise ConfirmationStateError("失败执行必须提供错误码")
                finished = record.finish_failure(now, error_code, retryable)
                event_type = "EXECUTION_UNKNOWN" if retryable else "EXECUTION_FAILED"
            await self._update_record(connection, finished, expected_version=record.version)
            await self._insert_event(
                connection, _event_for(finished, event_type, None, trace_id=trace_id)
            )
            return finished

    async def mark_unknown(
        self, confirmation_id: str, now: datetime, trace_id: str | None
    ) -> ConfirmationRecord:
        """对账任务将长时间 RUNNING 标为 UNKNOWN，不自动判定业务失败。"""

        async with self._database.engine.begin() as connection:
            row = await self._select_for_update_unscoped(connection, confirmation_id)
            record = _record_from_row(row)
            # 成功/确定拒绝可能与取消或对账并发完成，不得被迟到的 UNKNOWN 覆盖。
            if record.execution_status not in {"RUNNING", "FAILED_RETRYABLE"}:
                return record
            unknown = record.mark_unknown(now)
            await self._update_record(connection, unknown, expected_version=record.version)
            await self._insert_event(
                connection, _event_for(unknown, "EXECUTION_UNKNOWN", None, trace_id=trace_id)
            )
            return unknown

    async def claim_reconciliation_batch(
        self, *, older_than_seconds: int = 300, limit: int = 8, lease_seconds: int = 60
    ) -> list[ConfirmationRecord]:
        """原子领取到期的 Java 操作查询；持久化租约让多 Worker 公平分摊。"""

        if older_than_seconds < 1 or not 1 <= limit <= 500 or not 1 <= lease_seconds <= 3600:
            raise ValueError("对账领取参数无效")
        statement = text("""
            WITH due AS (
                SELECT id FROM agent_action_confirmations
                WHERE execution_status IN ('RUNNING', 'UNKNOWN', 'FAILED_RETRYABLE')
                  AND tool_id=ANY(CAST(:tools AS TEXT[]))
                  AND COALESCE(execution_started_at, finished_at, created_at)
                      <= CURRENT_TIMESTAMP - (:age * INTERVAL '1 second')
                  AND (reconciliation_next_attempt_at IS NULL
                       OR reconciliation_next_attempt_at <= CURRENT_TIMESTAMP)
                  AND (reconciliation_lease_until IS NULL
                       OR reconciliation_lease_until <= CURRENT_TIMESTAMP)
                ORDER BY COALESCE(reconciliation_next_attempt_at, execution_started_at,
                                  finished_at, created_at), id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE agent_action_confirmations c
            SET reconciliation_claim_token=:claim_token,
                reconciliation_lease_until=CURRENT_TIMESTAMP + (:lease * INTERVAL '1 second'),
                reconciliation_last_attempt_at=CURRENT_TIMESTAMP,
                reconciliation_attempts=reconciliation_attempts+1
            FROM due WHERE c.id=due.id RETURNING c.*
        """)
        async with self._database.engine.begin() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {
                            "tools": sorted(RECONCILABLE_TOOL_IDS),
                            "age": older_than_seconds,
                            "limit": limit,
                            "lease": lease_seconds,
                            "claim_token": str(uuid4()),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_record_from_row(row) for row in rows]

    async def finish_reconciliation_attempt(
        self,
        confirmation_id: str,
        claim_token: str,
        *,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
    ) -> bool:
        """只释放自己的查询租约，未核实结果指数退避；绝不改变业务执行状态。"""

        if not claim_token or not 1 <= retry_base_seconds <= retry_max_seconds <= 86400:
            raise ValueError("对账退避参数无效")
        statement = text("""
            UPDATE agent_action_confirmations
            SET reconciliation_claim_token=NULL, reconciliation_lease_until=NULL,
                reconciliation_next_attempt_at=CASE
                    WHEN execution_status IN ('RUNNING','UNKNOWN','FAILED_RETRYABLE') THEN
                        CURRENT_TIMESTAMP + (LEAST(:maximum,
                            :base * POWER(2, LEAST(GREATEST(reconciliation_attempts-1,0),20)))
                            * INTERVAL '1 second')
                    ELSE NULL END
            WHERE id=:id AND reconciliation_claim_token=:claim_token
        """)
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                statement,
                {
                    "id": confirmation_id,
                    "claim_token": claim_token,
                    "base": retry_base_seconds,
                    "maximum": retry_max_seconds,
                },
            )
            return bool(result.rowcount == 1)

    async def reconciliation_stats(self) -> dict[str, int | float]:
        """不含主体或业务 ID 的固定汇总，供后台缓存并更新监控指标。"""

        statement = text("""
            SELECT COUNT(*) FILTER (WHERE tool_id=ANY(CAST(:tools AS TEXT[]))) AS pending,
                COALESCE(GREATEST(0, EXTRACT(EPOCH FROM CURRENT_TIMESTAMP -
                    MIN(COALESCE(execution_started_at, finished_at, created_at))
                    FILTER (WHERE tool_id=ANY(CAST(:tools AS TEXT[]))))),0) AS oldest_age_seconds,
                COUNT(*) FILTER (WHERE tool_id IN ('fitness.memory.save.v1','fitness.memory.revoke.v1')
                    AND execution_status='UNKNOWN') AS manual
            FROM agent_action_confirmations
            WHERE execution_status IN ('RUNNING','UNKNOWN','FAILED_RETRYABLE')
        """)
        async with self._database.engine.connect() as connection:
            row = (
                (await connection.execute(statement, {"tools": sorted(RECONCILABLE_TOOL_IDS)}))
                .mappings()
                .one()
            )
        return {
            "pending": int(row["pending"]),
            "oldest_age_seconds": float(row["oldest_age_seconds"]),
            "manual": int(row["manual"]),
        }

    async def list_stale_running(
        self, *, older_than_seconds: int = 300, limit: int = 100
    ) -> list[ConfirmationRecord]:
        """列出长时间 RUNNING/UNKNOWN 的确认单，供对账任务持续查询下游结果。"""

        if older_than_seconds < 1 or limit < 1 or limit > 500:
            raise ValueError("对账扫描参数无效")
        statement = text(
            """
            SELECT * FROM agent_action_confirmations
            WHERE execution_status IN ('RUNNING', 'UNKNOWN', 'FAILED_RETRYABLE')
              AND COALESCE(execution_started_at, finished_at, created_at)
                  <= CURRENT_TIMESTAMP - (:age * INTERVAL '1 second')
            ORDER BY COALESCE(execution_started_at, finished_at, created_at), id
            LIMIT :limit
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (
                (await connection.execute(statement, {"age": older_than_seconds, "limit": limit}))
                .mappings()
                .all()
            )
        return [_record_from_row(row) for row in rows]

    async def list_events(self, confirmation_id: str) -> list[ConfirmationEvent]:
        """返回确认单事件，不包含 Token 或精确执行参数。"""

        statement = text(
            """
            SELECT * FROM agent_action_confirmation_events
            WHERE confirmation_id = :confirmation_id
            ORDER BY created_at, id
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (
                (await connection.execute(statement, {"confirmation_id": confirmation_id}))
                .mappings()
                .all()
            )
        return [_event_from_row(row) for row in rows]

    async def _select_for_update(
        self,
        connection: Any,
        confirmation_id: str,
        subject_user_id: str,
        organization_ids: Sequence[str],
    ) -> Any:
        statement = text(
            """
            SELECT * FROM agent_action_confirmations
            WHERE id = :id AND subject_user_id = :subject_user_id
              AND organization_id IN :organization_ids
            FOR UPDATE
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        row = (
            (
                await connection.execute(
                    statement,
                    {
                        "id": confirmation_id,
                        "subject_user_id": subject_user_id,
                        "organization_ids": list(organization_ids),
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfirmationNotFound("未找到确认单")
        return row

    async def _select_for_update_unscoped(self, connection: Any, confirmation_id: str) -> Any:
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM agent_action_confirmations WHERE id = :id FOR UPDATE"),
                    {"id": confirmation_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfirmationNotFound("未找到确认单")
        return row

    async def _select_by_request(self, connection: Any, request_id: str) -> Any:
        return (
            (
                await connection.execute(
                    text("SELECT * FROM agent_action_confirmations WHERE request_id = :request_id"),
                    {"request_id": request_id},
                )
            )
            .mappings()
            .first()
        )

    async def _update_record(
        self, connection: Any, record: ConfirmationRecord, expected_version: int
    ) -> None:
        statement = text(
            """
            UPDATE agent_action_confirmations SET
                authorization_status = :authorization_status, execution_status = :execution_status,
                version = :version, approved_at = :approved_at, rejected_at = :rejected_at,
                cancelled_at = :cancelled_at,
                execution_started_at = :execution_started_at, finished_at = :finished_at,
                decision_request_id = :decision_request_id, revocation_request_id = :revocation_request_id,
                credential_jti = :credential_jti,
                credential_consumed_at = :credential_consumed_at, last_error_code = :last_error_code
            WHERE id = :id AND version = :expected_version
            """
        )
        result = await connection.execute(
            statement,
            {
                "id": record.id,
                "authorization_status": record.authorization_status,
                "execution_status": record.execution_status,
                "version": record.version,
                "approved_at": record.approved_at,
                "rejected_at": record.rejected_at,
                "cancelled_at": record.cancelled_at,
                "execution_started_at": record.execution_started_at,
                "finished_at": record.finished_at,
                "decision_request_id": record.decision_request_id,
                "revocation_request_id": record.revocation_request_id,
                "credential_jti": record.credential_jti,
                "credential_consumed_at": record.credential_consumed_at,
                "last_error_code": record.last_error_code,
                "expected_version": expected_version,
            },
        )
        if result.rowcount != 1:
            raise ConfirmationStateError("确认单已被其他请求修改")

    async def _insert_event(self, connection: Any, event: ConfirmationEvent) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO agent_action_confirmation_events (
                    confirmation_id, event_type, actor_user_id, request_id, decision_request_id,
                    trace_id, authorization_status, execution_status, authorization_version,
                    actor_roles, actor_organization_ids
                ) VALUES (
                    :confirmation_id, :event_type, :actor_user_id, :request_id, :decision_request_id,
                    :trace_id, :authorization_status, :execution_status, :authorization_version,
                    :actor_roles, :actor_organization_ids
                )
                """
            ),
            {
                "confirmation_id": event.confirmation_id,
                "event_type": event.event_type,
                "actor_user_id": event.actor_user_id,
                "request_id": event.request_id,
                "decision_request_id": event.decision_request_id,
                "trace_id": event.trace_id,
                "authorization_status": event.authorization_status,
                "execution_status": event.execution_status,
                "authorization_version": event.authorization_version,
                "actor_roles": list(event.actor_roles),
                "actor_organization_ids": list(event.actor_organization_ids),
            },
        )


def _action_params(
    confirmation_id: str, action: ConfirmationAction, expires_at: datetime
) -> dict[str, Any]:
    return {
        "id": confirmation_id,
        "thread_id": action.thread_id,
        "subject_user_id": action.subject_user_id,
        "organization_id": action.organization_id,
        "tool_id": action.tool_id,
        "risk_level": action.risk_level,
        "action": action.action,
        "resource_type": action.resource_type,
        "resource_id": action.resource_id,
        "expected_resource_version": action.expected_resource_version,
        "request_id": action.request_id,
        "payload_hash": action.payload_hash,
        "display_summary": _json_dumps(action.display_summary),
        "payload_ciphertext": action.payload_ciphertext,
        "payload_key_version": action.payload_key_version,
        "expires_at": expires_at,
        "actor_roles": list(action.actor_roles),
        "actor_organization_ids": list(action.actor_organization_ids),
        "transport": action.transport,
        "contract_version": action.contract_version,
    }


def _json_dumps(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _same_action(row: Any, action: ConfirmationAction) -> bool:
    return bool(
        row["tool_id"] == action.tool_id
        and row["organization_id"] == action.organization_id
        and row["action"] == action.action
        and row["resource_type"] == action.resource_type
        and row["resource_id"] == action.resource_id
        and row["expected_resource_version"] == action.expected_resource_version
        and row["payload_hash"] == action.payload_hash
        and row["payload_key_version"] == action.payload_key_version
        and row["subject_user_id"] == action.subject_user_id
        and tuple(row["actor_roles"] or ()) == action.actor_roles
        and tuple(row["actor_organization_ids"] or ()) == action.actor_organization_ids
        and _json_dumps(row["display_summary"]) == _json_dumps(action.display_summary)
    )


def _record_from_row(row: Any) -> ConfirmationRecord:
    return ConfirmationRecord(
        id=row["id"],
        protocol_version=row["protocol_version"],
        thread_id=row["thread_id"],
        subject_user_id=row["subject_user_id"],
        organization_id=row["organization_id"],
        tool_id=row["tool_id"],
        risk_level=row["risk_level"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        expected_resource_version=row["expected_resource_version"],
        request_id=row["request_id"],
        payload_hash=row["payload_hash"],
        display_summary=row["display_summary"],
        payload_ciphertext=bytes(row["payload_ciphertext"]),
        payload_key_version=row["payload_key_version"],
        authorization_status=cast(AuthorizationStatus, row["authorization_status"]),
        execution_status=cast(ExecutionStatus, row["execution_status"]),
        version=row["version"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        approved_at=row["approved_at"],
        rejected_at=row["rejected_at"],
        cancelled_at=row["cancelled_at"],
        execution_started_at=row["execution_started_at"],
        finished_at=row["finished_at"],
        decision_request_id=row["decision_request_id"],
        revocation_request_id=row["revocation_request_id"],
        credential_jti=row["credential_jti"],
        credential_consumed_at=row["credential_consumed_at"],
        last_error_code=row["last_error_code"],
        actor_roles=tuple(row["actor_roles"] or ()),
        actor_organization_ids=tuple(row["actor_organization_ids"] or ()),
        transport=row.get("transport", "http"),
        contract_version=row.get("contract_version", "v1"),
        reconciliation_next_attempt_at=row.get("reconciliation_next_attempt_at"),
        reconciliation_last_attempt_at=row.get("reconciliation_last_attempt_at"),
        reconciliation_attempts=row.get("reconciliation_attempts", 0),
        reconciliation_lease_until=row.get("reconciliation_lease_until"),
        reconciliation_claim_token=row.get("reconciliation_claim_token"),
    )


def _event_for(
    record: ConfirmationRecord,
    event_type: ConfirmationEventType,
    action: ConfirmationAction | None,
    *,
    trace_id: str | None = None,
    actor_user_id: str | None = None,
    actor_roles: Sequence[str] = (),
) -> ConfirmationEvent:
    return ConfirmationEvent(
        id=None,
        confirmation_id=record.id,
        event_type=event_type,
        actor_user_id=actor_user_id or (action.subject_user_id if action is not None else None),
        request_id=record.request_id,
        decision_request_id=record.decision_request_id,
        trace_id=trace_id,
        authorization_status=record.authorization_status,
        execution_status=record.execution_status,
        authorization_version=record.version,
        actor_roles=tuple(actor_roles),
        actor_organization_ids=(
            action.actor_organization_ids
            if action is not None and action.actor_organization_ids
            else (record.organization_id,)
        ),
    )


def _event_from_row(row: Any) -> ConfirmationEvent:
    return ConfirmationEvent(
        id=row["id"],
        confirmation_id=row["confirmation_id"],
        event_type=row["event_type"],
        actor_user_id=row["actor_user_id"],
        request_id=row["request_id"],
        decision_request_id=row["decision_request_id"],
        trace_id=row["trace_id"],
        authorization_status=cast(AuthorizationStatus, row["authorization_status"]),
        execution_status=cast(ExecutionStatus, row["execution_status"]),
        authorization_version=row["authorization_version"],
        actor_roles=tuple(row["actor_roles"] or ()),
        actor_organization_ids=tuple(row["actor_organization_ids"] or ()),
        created_at=row["created_at"],
    )
