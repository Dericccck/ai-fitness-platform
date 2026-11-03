"""仅允许隔离临时库的真实 PG/Redis 验收，不碰开发库或真实业务。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.core.config import Settings
from app.infrastructure.cache import Cache
from app.infrastructure.database import Database
from app.infrastructure.model_protection import ModelCapacityError, RedisModelLease
from app.infrastructure.model_usage import ModelUsage, ModelUsageRepository
from app.streaming.control import RunControl, run_control
from app.streaming.repository import ChatRunRepository, RunConflict, RunSessionBusy
from app.streaming.service import ChatRunManager
from tests.test_confirmation_models import action

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RUNTIME_DELIVERY_TESTS") != "1", reason="需要隔离临时库验收脚本显式开启"
)


def isolated_settings() -> Settings:
    settings = Settings(_env_file=None)
    from sqlalchemy.engine import make_url

    assert (make_url(settings.database_url).database or "").startswith(
        "fitness_runtime_acceptance_"
    )
    return settings


async def test_concurrent_claim_encryption_owner_and_terminal_fencing() -> None:
    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    run_id = str(uuid4())
    try:
        claims = await asyncio.gather(*(repo.claim(run_id, "owner", "digest") for _ in range(20)))
        assert sum(created for _, created in claims) == 1
        assert await repo.get(run_id, "stranger") is None
        for owner, digest in (("stranger", "digest"), ("owner", "changed")):
            with pytest.raises(RunConflict):
                await repo.claim(run_id, owner, digest)
        expected = {"answer": "SECRET_TEXT", "status": "CONFIRMATION_REQUIRED"}
        assert await repo.finish(run_id, "owner", status="COMPLETED", result=expected)
        assert not await repo.finish(run_id, "owner", status="FAILED", error_code="LATE")
        assert (await repo.get(run_id, "owner")).result == expected
        repo.cipher = AesGcmPayloadCipher(b"y" * 32, "test-v2", {"test-v1": b"x" * 32})
        assert (await repo.get(run_id, "owner")).result == expected
        async with database.engine.connect() as connection:
            raw = (
                await connection.execute(
                    text("SELECT result_ciphertext FROM agent_chat_runs WHERE run_id=:id"),
                    {"id": run_id},
                )
            ).scalar_one()
            assert b"SECRET_TEXT" not in bytes(raw)
    finally:
        await database.close()


async def test_lost_run_is_unknown_never_reclaimed_and_expiry_cleanup() -> None:
    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    run_id = str(uuid4())
    try:
        await repo.claim(run_id, "owner", "digest")
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_runs SET execution_deadline=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run_id},
            )
        assert (await repo.get(run_id, "owner")).status == "UNKNOWN"
        run, created = await repo.claim(run_id, "owner", "digest")
        assert run.status == "UNKNOWN" and not created
        assert not await repo.finish(run_id, "owner", status="COMPLETED", result={"answer": "late"})
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_runs SET expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run_id},
            )
        with pytest.raises(RunConflict):
            await repo.claim(run_id, "owner", "digest")
        assert await repo.cleanup() == 1
    finally:
        await database.close()


async def test_real_usage_ledger_is_idempotent_and_preserves_decimal() -> None:
    database = Database(isolated_settings())
    repo = ModelUsageRepository(database)
    usage = ModelUsage(
        uuid4().hex, "test-request", None, "test", "test", "chat", 1, "STARTED", "v1"
    )
    try:
        await repo.write(usage)
        done = replace(
            usage,
            status="SUCCEEDED",
            input_tokens=100,
            output_tokens=20,
            cache_hit_tokens=60,
            cache_miss_tokens=40,
            estimated_cost="0.000123456789",
            currency="CNY",
            pricing_version="test-v1",
        )
        await repo.write(done)
        await repo.write(usage)  # 迟到 STARTED 不回滚终态
        await repo.write(done)
        async with database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text("SELECT * FROM agent_model_usage WHERE call_id=:id"),
                        {"id": usage.call_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 1 and rows[0]["status"] == "SUCCEEDED"
        assert str(rows[0]["estimated_cost"]) == "0.000123456789"
        assert rows[0]["input_tokens"] == 100
    finally:
        await database.close()


async def test_redis_shared_slots_across_instances_and_cancel_release() -> None:
    settings = isolated_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    namespace = "acceptance:" + uuid4().hex
    a = RedisModelLease(client, provider=namespace, limit=2, ttl=5)
    b = RedisModelLease(client, provider=namespace, limit=2, ttl=5)
    try:
        async with a.hold(), b.hold():
            with pytest.raises(ModelCapacityError):
                async with a.hold():
                    pass
        assert await client.zcard(a.key) == 0
        entered = asyncio.Event()

        async def hold() -> None:
            async with a.hold():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await client.zcard(a.key) == 0
    finally:
        await client.delete(a.key)
        await client.aclose()


async def test_redis_atomic_quota_only_allows_configured_requests() -> None:
    cache = Cache(isolated_settings().redis_url)
    key = "acceptance:quota:" + uuid4().hex
    try:
        results = await asyncio.gather(
            *(cache.consume_fixed_window(key, limit=5, window_seconds=60) for _ in range(30))
        )
        assert sum(results) == 5
    finally:
        await cache.client.delete(key)
        await cache.close()


async def test_real_cancel_completion_race_has_one_canonical_terminal() -> None:
    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    try:
        for _ in range(12):
            run_id = str(uuid4())
            await repo.claim(run_id, "owner", "digest")
            saved, accepted = await asyncio.gather(
                repo.finish(run_id, "owner", status="COMPLETED", result={"answer": "done"}),
                repo.request_cancel(run_id, "owner", "stop"),
            )
            if saved:
                assert accepted.status == "COMPLETED"
                assert not await repo.finish_cancelled(run_id, "owner")
            else:
                assert accepted.status == "CANCEL_REQUESTED"
                assert await repo.finish_cancelled(run_id, "owner")
            assert not await repo.finish(run_id, "owner", status="FAILED", error_code="LATE")
            assert (await repo.get(run_id, "owner")).status in {"COMPLETED", "CANCELLED"}
    finally:
        await database.close()


async def test_real_cancel_owner_idempotency_active_scope_and_deadline() -> None:
    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    run_id, scope = str(uuid4()), str(uuid4())
    try:
        await repo.claim(run_id, "owner", "digest", scope=scope)
        assert await repo.request_cancel(run_id, "stranger", "stop") is None
        accepted = await repo.request_cancel(run_id, "owner", "stop")
        repeated = await repo.request_cancel(run_id, "owner", "different-stop")
        assert repeated.cancel_requested_at == accepted.cancel_requested_at
        with pytest.raises(RunSessionBusy):
            await repo.claim(str(uuid4()), "owner", "digest", scope=scope)
        async with database.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE agent_chat_runs SET cancel_deadline=CURRENT_TIMESTAMP-INTERVAL '1 second'
                WHERE run_id=:id
            """),
                {"id": run_id},
            )
        expired = await repo.get(run_id, "owner")
        assert expired.status == "UNKNOWN" and expired.error_code == "CANCEL_ACK_TIMEOUT"
        assert expired.cancel_requested_at is not None
        assert not await repo.finish_cancelled(run_id, "owner")
    finally:
        await database.close()


async def test_real_cross_instance_cancel_revokes_only_source_confirmation() -> None:
    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    confirmations = ConfirmationRepository(database)
    run_id, card_id, other_id = str(uuid4()), str(uuid4()), str(uuid4())
    a = ChatRunManager(
        repo,
        timeout=60,
        heartbeat=0.01,
        queue_size=8,
        shutdown_timeout=0.1,
        cancel_confirmations=confirmations.cancel_for_run,
        poll_interval=0.02,
    )
    b = ChatRunManager(repo, timeout=60, heartbeat=0.01, queue_size=8, shutdown_timeout=0.1)
    entered = asyncio.Event()

    async def execute(emit: object) -> dict[str, object]:
        await confirmations.create(
            card_id,
            replace(action(), request_id=str(uuid4())),
            datetime.now(UTC) + timedelta(minutes=5),
        )
        entered.set()
        await asyncio.Event().wait()
        return {}

    try:
        await confirmations.create(
            other_id,
            replace(action(), request_id=str(uuid4())),
            datetime.now(UTC) + timedelta(minutes=5),
        )
        await a.start(run_id, "owner", "digest", "request", execute)
        task = a._tasks[run_id]
        await asyncio.wait_for(entered.wait(), 2)
        assert (await b.cancel(run_id, "owner", "stop")).status == "CANCEL_REQUESTED"
        await asyncio.wait_for(task, 3)
        assert (await repo.get(run_id, "owner")).status == "CANCELLED"
        assert (
            await confirmations.get_for_subject(card_id, "student-1", ["org-1"])
        ).authorization_status == "CANCELLED"
        assert (
            await confirmations.get_for_subject(other_id, "student-1", ["org-1"])
        ).authorization_status == "PENDING"
        with run_control(RunControl(run_id, "owner", repo)), pytest.raises(asyncio.CancelledError):
            await confirmations.create(
                str(uuid4()),
                replace(action(), request_id=str(uuid4())),
                datetime.now(UTC) + timedelta(minutes=5),
            )
        assert await database.runtime_schema_ready()
    finally:
        await a.close()
        await b.close()
        # 只清理本测试生成的确认事实，保留生产迁移对撤销审计的回滚保护。
        async with database.engine.begin() as connection:
            await connection.execute(
                text("""
                DELETE FROM agent_action_confirmation_events
                WHERE confirmation_id=ANY(CAST(:ids AS TEXT[]))
            """),
                {"ids": [card_id, other_id]},
            )
            await connection.execute(
                text("""
                DELETE FROM agent_action_confirmations WHERE id=ANY(CAST(:ids AS TEXT[]))
            """),
                {"ids": [card_id, other_id]},
            )
        await database.close()


async def test_completed_history_and_summary_survive_result_cleanup() -> None:
    from app.session_summary import SessionSummaryRepository
    from app.streaming.completion import SummaryDraft, TurnCompletion

    database = Database(isolated_settings())
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = ChatRunRepository(database, cipher, timeout=60, retention_hours=1)
    run_id, thread, subject = str(uuid4()), str(uuid4()), str(uuid4())
    summary = SummaryDraft(
        thread, subject, b"encrypted-test", "test-v1", "a" * 64, 12, 7, 0, "正式摘要"
    )
    try:
        await repo.claim(run_id, "owner", "d")
        assert await repo.finish(
            run_id,
            "owner",
            status="COMPLETED",
            result={"answer": "ok"},
            completion=TurnCompletion(thread, subject, 0, summary),
        )
        saved = await SessionSummaryRepository(database).get_for_subject(thread, subject)
        assert saved.source_run_id == run_id and saved.summary_version == 1
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_runs SET expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run_id},
            )
        assert await repo.get(run_id, "owner") is None
        await repo.cleanup()
        commit = await repo.history_commit(thread, "owner", subject, run_id)
        assert commit.run_id == run_id and commit.version == 1
        assert await repo.history_commit(thread, "stranger", subject) is None
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_history_commits SET checked_at=CURRENT_TIMESTAMP-INTERVAL '2 hours' WHERE thread_id=:thread"
                ),
                {"thread": thread},
            )

        async def present(ids: list[str]) -> set[str]:
            return set(ids)

        await repo.cleanup_history(present)
        assert await repo.history_commit(thread, "owner", subject) is not None
        assert thread in await SessionSummaryRepository(database).list_thread_ids_for_subject(
            subject
        )
        await SessionSummaryRepository(database).invalidate_for_subject(subject)
        assert await repo.history_commit(thread, "owner", subject) is not None
    finally:
        await database.close()


async def test_cancelled_candidate_never_overwrites_committed_summary() -> None:
    from app.session_summary import SessionSummaryRepository
    from app.streaming.completion import SummaryDraft, TurnCompletion

    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    thread, subject, old_run, new_run = [str(uuid4()) for _ in range(4)]
    try:
        await repo.claim(old_run, "owner", "d")
        draft = SummaryDraft(thread, subject, b"old", "test-v1", "b" * 64, 12, 7, 0, "old")
        assert await repo.finish(
            old_run,
            "owner",
            status="COMPLETED",
            result={"answer": "ok"},
            completion=TurnCompletion(thread, subject, 0, draft),
        )
        await repo.claim(new_run, "owner", "d")
        candidate = replace(
            draft, summary_ciphertext=b"cancelled", summary_hash="c" * 64, base_version=1
        )
        await repo.request_cancel(new_run, "owner", "stop")
        assert not await repo.finish(
            new_run,
            "owner",
            status="COMPLETED",
            result={"answer": "ok"},
            completion=TurnCompletion(thread, subject, 1, candidate),
        )
        assert await repo.finish_cancelled(new_run, "owner")
        saved = await SessionSummaryRepository(database).get_for_subject(thread, subject)
        assert saved.summary_ciphertext == b"old" and saved.source_run_id == old_run
        assert (await repo.history_commit(thread, "owner", subject)).run_id == old_run
    finally:
        await database.close()


async def test_summary_version_conflict_rolls_back_run_and_history_commit() -> None:
    from app.streaming.completion import SummaryDraft, TurnCompletion

    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    run_id, thread, subject = [str(uuid4()) for _ in range(3)]
    try:
        await repo.claim(run_id, "owner", "d")
        invalid = SummaryDraft(
            thread, subject, b"candidate", "test-v1", "a" * 64, 12, 7, 5, "candidate"
        )
        with pytest.raises(RuntimeError, match="版本"):
            await repo.finish(
                run_id,
                "owner",
                status="COMPLETED",
                result={"answer": "ok"},
                completion=TurnCompletion(thread, subject, 0, invalid),
            )
        assert (await repo.get(run_id, "owner")).status == "RUNNING"
        assert await repo.history_commit(thread, "owner", subject) is None
        assert await repo.finish(
            run_id,
            "owner",
            status="COMPLETED",
            result={"answer": "ok"},
            completion=TurnCompletion(thread, subject, 0),
        )
        late_run = str(uuid4())
        await repo.claim(late_run, "owner", "d")
        with pytest.raises(RunConflict):
            await repo.finish(
                late_run,
                "owner",
                status="COMPLETED",
                result={"answer": "ok"},
                completion=TurnCompletion(thread, subject, 0),
            )
        assert (await repo.get(late_run, "owner")).status == "RUNNING"
        assert (await repo.history_commit(thread, "owner", subject)).run_id == run_id
    finally:
        await database.close()


async def test_real_completion_cancel_race_commits_summary_and_history_as_one_fact() -> None:
    from app.session_summary import SessionSummaryRepository
    from app.streaming.completion import SummaryDraft, TurnCompletion

    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    summaries = SessionSummaryRepository(database)
    try:
        for _ in range(8):
            run, thread, subject = [str(uuid4()) for _ in range(3)]
            await repo.claim(run, "owner", "digest")
            draft = SummaryDraft(thread, subject, b"cipher", "test-v1", "d" * 64, 12, 7, 0, "候选")
            results = await asyncio.gather(
                repo.request_cancel(run, "owner", "stop"),
                repo.finish(
                    run,
                    "owner",
                    status="COMPLETED",
                    result={"answer": "ok"},
                    completion=TurnCompletion(thread, subject, 0, draft),
                ),
            )
            final = await repo.get(run, "owner")
            commit = await repo.history_commit(thread, "owner", subject)
            summary = await summaries.get_for_subject(thread, subject)
            if final.status == "COMPLETED":
                assert results[1] and commit.run_id == run and summary.source_run_id == run
            else:
                assert final.status == "CANCEL_REQUESTED" and not results[1]
                assert commit is None and summary is None
                await repo.finish_cancelled(run, "owner")
        # 同一会话的下一候选按基准版本更新，不覆盖更高版本。
        first, second, thread, subject = [str(uuid4()) for _ in range(4)]
        await repo.claim(first, "owner", "d")
        draft = SummaryDraft(thread, subject, b"first", "test-v1", "e" * 64, 12, 7, 0, "first")
        assert await repo.finish(
            first,
            "owner",
            status="COMPLETED",
            result={"answer": "first"},
            completion=TurnCompletion(thread, subject, 0, draft),
        )
        await repo.claim(second, "owner", "d")
        assert await repo.finish(
            second,
            "owner",
            status="COMPLETED",
            result={"answer": "second"},
            completion=TurnCompletion(
                thread, subject, 1, replace(draft, base_version=1, summary_ciphertext=b"second")
            ),
        )
        assert (await summaries.get_for_subject(thread, subject)).summary_version == 2
        assert (await repo.history_commit(thread, "owner", subject)).version == 2
    finally:
        await database.close()


async def test_legacy_expired_result_backfill_and_unreferenced_history_cleanup() -> None:
    from app.streaming.repository import request_digest

    database = Database(isolated_settings())
    repo = ChatRunRepository(
        database, AesGcmPayloadCipher(b"x" * 32, "test-v1"), timeout=60, retention_hours=1
    )
    run, thread, subject = [str(uuid4()) for _ in range(3)]
    scope = request_digest({"owner": "owner", "thread": thread})
    try:
        await repo.claim(run, "owner", "d", scope=scope)
        assert await repo.finish(run, "owner", status="COMPLETED", result={"answer": "legacy"})
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_runs SET expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run},
            )
        assert await repo.get(run, "owner") is None
        assert await repo.history_commit("foreign-thread", "owner", subject, run) is None
        assert (await repo.history_commit(thread, "owner", subject, run)).run_id == run
        await repo.cleanup()
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_history_commits SET checked_at=CURRENT_TIMESTAMP-INTERVAL '2 hours' WHERE thread_id=:thread"
                ),
                {"thread": thread},
            )
        pending = str(uuid4())
        await repo.claim(pending, "owner", "d", scope=scope)

        async def absent(ids: list[str]) -> set[str]:
            return set()

        assert await repo.cleanup_history(absent) == 0
        assert await repo.finish(pending, "owner", status="FAILED", error_code="TEST")
        assert await repo.cleanup_history(absent) == 1
        assert await repo.history_commit(thread, "owner", subject) is None
    finally:
        await database.close()
