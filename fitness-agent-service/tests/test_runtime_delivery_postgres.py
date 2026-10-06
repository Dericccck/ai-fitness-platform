"""仅允许隔离临时库的真实 PG/Redis 验收，不碰开发库或真实业务。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.cache import Cache
from app.infrastructure.database import Database
from app.infrastructure.model_protection import ModelCapacityError, RedisModelLease
from app.infrastructure.model_usage import ModelUsage, ModelUsageRepository
from app.streaming.repository import ChatRunRepository, RunConflict

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
