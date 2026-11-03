"""确认执行路由与 UNKNOWN 的真实 PostgreSQL 验收，仅运行在独立临时库。"""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.confirmation.models import ConfirmationStateError
from app.confirmation.repository import ConfirmationRepository
from app.core.config import Settings
from app.infrastructure.database import Database

from .test_confirmation_models import action

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RUNTIME_DELIVERY_TESTS") != "1", reason="仅允许隔离临时库验收脚本开启"
)


async def test_persisted_execution_route_original_jti_and_unknown_are_fenced() -> None:
    settings = Settings(_env_file=None)
    assert (make_url(settings.database_url).database or "").startswith(
        "fitness_runtime_acceptance_"
    )
    database = Database(settings)
    repository = ConfirmationRepository(database)
    now = datetime.now(UTC)
    confirmation_id, request_id = str(uuid4()), str(uuid4())
    original = replace(
        action(),
        request_id=request_id,
        actor_roles=("COACH",),
        actor_organization_ids=("org-1",),
        transport="mcp",
        contract_version="1",
    )
    try:
        saved = await repository.create(confirmation_id, original, now + timedelta(minutes=5))
        assert (saved.transport, saved.contract_version) == ("mcp", "1")
        # 同一业务请求在发布回退后仍复用已经固定的执行路由。
        duplicate = await repository.create(
            str(uuid4()),
            replace(original, transport="http", contract_version="v1"),
            saved.expires_at,
        )
        assert duplicate.id == saved.id
        assert (duplicate.transport, duplicate.contract_version) == ("mcp", "1")
        await repository.decide(
            saved.id,
            saved.subject_user_id,
            ["org-1"],
            "APPROVE",
            "decision",
            now,
            "trace",
            saved.subject_user_id,
            ["COACH"],
        )
        await repository.issue_credential_jti(saved.id, "original-jti", now, "trace")
        attempts = await asyncio.gather(
            *(
                repository.claim_execution(saved.id, now - timedelta(seconds=10), "trace")
                for _ in range(8)
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, Exception) for value in attempts) == 1
        unknown = await repository.finish_execution(
            saved.id, False, now, "trace", error_code="LOST_WRITE_RESPONSE", retryable=True
        )
        assert unknown.execution_status == "UNKNOWN"
        assert unknown.credential_jti == "original-jti"
        assert unknown.credential_consumed_at is not None
        assert unknown.request_id == request_id
        assert (unknown.transport, unknown.contract_version) == ("mcp", "1")
        with pytest.raises(ConfirmationStateError):
            await repository.requeue_retryable(saved.id, "retry")
        stale = await repository.list_stale_running(older_than_seconds=1)
        assert saved.id in {row.id for row in stale}
        events = await repository.list_events(saved.id)
        assert [event.event_type for event in events].count("CONSUMED") == 1
        assert events[-1].event_type == "EXECUTION_UNKNOWN"
        converged = await repository.finish_execution(saved.id, True, now, "reconciled")
        assert converged.execution_status == "SUCCEEDED"
        # 取消清理或迟到的超时不能覆盖已核实的成功。
        assert (
            await repository.mark_unknown(saved.id, now, "late-cancel")
        ).execution_status == "SUCCEEDED"
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_action_confirmations WHERE id=:id"), {"id": confirmation_id}
            )
        await database.close()


async def test_reconciliation_claims_are_fair_scoped_and_fenced_across_workers() -> None:
    settings = Settings(_env_file=None)
    assert (make_url(settings.database_url).database or "").startswith(
        "fitness_runtime_acceptance_"
    )
    database = Database(settings)
    repository = ConfirmationRepository(database)
    now = datetime.now(UTC)
    ids = [str(uuid4()) for _ in range(6)]
    try:
        for index, confirmation_id in enumerate(ids):
            tool_id = "fitness.memory.save.v1" if index == 5 else "fitness.booking.create.v1"
            await repository.create(
                confirmation_id,
                replace(
                    action(),
                    request_id=str(uuid4()),
                    tool_id=tool_id,
                    transport="local" if index == 5 else "mcp",
                    contract_version="v1" if index == 5 else "1",
                ),
                now + timedelta(minutes=5),
            )
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("""
                    UPDATE agent_action_confirmations SET execution_status='UNKNOWN',
                        execution_started_at=CURRENT_TIMESTAMP - (:age * INTERVAL '1 second')
                    WHERE id=:id
                """),
                    {"id": confirmation_id, "age": 100 - index},
                )
        groups = await asyncio.gather(
            *(
                repository.claim_reconciliation_batch(older_than_seconds=1, limit=2)
                for _ in range(2)
            )
        )
        claimed = [record for group in groups for record in group]
        assert len(claimed) == 4 and len({record.id for record in claimed}) == 4
        assert ids[5] not in {record.id for record in claimed}
        for record in claimed:
            assert record.reconciliation_attempts == 1
            assert record.reconciliation_last_attempt_at is not None
            assert record.reconciliation_lease_until > now
            assert await repository.finish_reconciliation_attempt(
                record.id, record.reconciliation_claim_token
            )
        # 四条永久 UNKNOWN 已退避；较新的成功操作不被它们占住前一批。
        later = await repository.claim_reconciliation_batch(older_than_seconds=1, limit=2)
        assert len(later) == 1 and later[0].id == ids[4]
        await repository.finish_execution(later[0].id, True, now, "durable-fact")
        assert await repository.finish_reconciliation_attempt(
            later[0].id, later[0].reconciliation_claim_token
        )
        summary = await repository.reconciliation_stats()
        assert summary["pending"] == 4 and summary["manual"] == 1
        assert summary["oldest_age_seconds"] >= 99
        assert await repository.claim_reconciliation_batch(older_than_seconds=1, limit=10) == []

        expired = claimed[0]
        async with database.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE agent_action_confirmations
                SET reconciliation_next_attempt_at=CURRENT_TIMESTAMP-INTERVAL '1 second',
                    reconciliation_claim_token='expired-worker',
                    reconciliation_lease_until=CURRENT_TIMESTAMP-INTERVAL '1 second'
                WHERE id=:id
            """),
                {"id": expired.id},
            )
        recovered = (await repository.claim_reconciliation_batch(older_than_seconds=1, limit=1))[0]
        assert recovered.id == expired.id and recovered.reconciliation_attempts == 2
        assert not await repository.finish_reconciliation_attempt(recovered.id, "expired-worker")
        assert await repository.finish_reconciliation_attempt(
            recovered.id, recovered.reconciliation_claim_token
        )
        row = await repository.get_for_subject(recovered.id, recovered.subject_user_id, ["org-1"])
        delay = (row.reconciliation_next_attempt_at - datetime.now(UTC)).total_seconds()
        assert 58 < delay <= 60
        assert row.execution_status == "UNKNOWN"
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_action_confirmations WHERE id=ANY(CAST(:ids AS TEXT[]))"),
                {"ids": ids},
            )
        await database.close()
