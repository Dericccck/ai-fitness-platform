"""提取任务的真实 PG 租约、重试上限和撤销屏障。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.extraction_worker import MemoryExtractionWorker
from app.memory.source import MemorySourceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1", reason="仅在隔离 PostgreSQL 中启用"
)


@pytest.mark.asyncio
async def test_extraction_lease_retry_limit_and_revoked_source_fence():
    database = Database(Settings(_env_file=None))
    cipher = AesGcmPayloadCipher(b"8" * 32, "test")
    sources = MemorySourceRepository(database, cipher)
    calls = []

    class Extractor:
        async def propose_controlled(self, message):
            calls.append(message)
            raise AssertionError("撤销后的来源不能发送给模型")

    service = SimpleNamespace(extractor=Extractor())
    worker = MemoryExtractionWorker(database, cipher, service, max_attempts=2)
    suffix = uuid4().hex
    subject, org = f"worker-user-{suffix}", f"worker-org-{suffix}"
    identity = AgentIdentity(
        subject=subject,
        organization_ids=frozenset({org}),
        roles=frozenset(),
        issued_at=1,
        expires_at=2,
    )
    try:
        source = await sources.capture(
            identity=identity,
            thread_id="thread",
            source_request_id="source",
            message="周末有时间训练",
        )
        assert source is not None and await sources.complete(source)
        claims = await asyncio.gather(worker._claim(), worker._claim())
        assert sum(claim is not None for claim in claims) == 1
        claim = next(claim for claim in claims if claim is not None)
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_memory_profiles SET write_epoch=write_epoch+1 "
                    "WHERE subject_user_id=:subject AND organization_id=:org"
                ),
                {"subject": subject, "org": org},
            )
        await worker._process(claim)
        assert calls == []
        async with database.engine.begin() as connection:
            status = (
                await connection.execute(
                    text("SELECT status FROM agent_memory_extraction_jobs WHERE id=:id"),
                    {"id": claim["id"]},
                )
            ).scalar_one()
            assert status == "DONE"
            await connection.execute(
                text(
                    "UPDATE agent_memory_extraction_jobs SET status='RUNNING',attempts=2,"
                    "lease_token='expired',lease_until=CURRENT_TIMESTAMP - INTERVAL '1 second' "
                    "WHERE id=:id"
                ),
                {"id": claim["id"]},
            )
        assert await worker._claim() is None
        async with database.engine.connect() as connection:
            job = (
                (
                    await connection.execute(
                        text(
                            "SELECT status,lease_token FROM agent_memory_extraction_jobs WHERE id=:id"
                        ),
                        {"id": claim["id"]},
                    )
                )
                .mappings()
                .one()
            )
            assert job["status"] == "FAILED" and job["lease_token"] is None
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_memory_source_messages WHERE subject_user_id=:subject"),
                {"subject": subject},
            )
            await connection.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id=:subject"),
                {"subject": subject},
            )
        await database.close()
