"""Run only against an isolated, migrated PostgreSQL test database."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.models import MemoryValidationError
from app.memory.repository import MemoryRepository, MemorySettings, MemoryVersionConflictError
from app.memory.source import MemorySourceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="requires an explicitly configured isolated PostgreSQL test database",
)


@pytest.mark.asyncio
async def test_settings_fence_stale_sources_summary_and_concurrent_updates() -> None:
    db = Database(Settings(_env_file=None))
    repo = MemoryRepository(db)
    sources = MemorySourceRepository(db, AesGcmPayloadCipher(b"1" * 32, "test"))
    suffix = uuid4().hex
    subject, org = f"settings-user-{suffix}", f"settings-org-{suffix}"
    identity = AgentIdentity(subject, frozenset({org}), frozenset(), 1, 2)
    params = {"subject": subject, "org": org, "thread": f"settings-{suffix}"}
    try:
        assert await repo.get_settings(identity=identity, organization_id=org) == MemorySettings()
        source = await sources.capture(
            identity=identity,
            thread_id=params["thread"],
            source_request_id="req",
            message="周末训练",
        )
        assert source is not None
        async with db.engine.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO agent_session_summaries
                  (thread_id,subject_user_id,summary_ciphertext,summary_key_version,summary_hash,retention_until)
                VALUES (:thread,:subject,:body,'test',:hash,now() + interval '1 day')
            """),
                {**params, "body": b"ciphertext", "hash": "a" * 64},
            )
        disabled = await repo.update_settings(
            identity=identity,
            organization_id=org,
            expected_revision=0,
            extraction_enabled=False,
            personalization_enabled=False,
        )
        assert disabled == MemorySettings(False, False, 1)
        assert await sources.complete(source) is False
        assert (
            await sources.capture(
                identity=identity,
                thread_id=params["thread"],
                source_request_id="req-2",
                message="不保存",
            )
            is None
        )
        async with db.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("""
                SELECT p.write_epoch, f.revision AS fence_revision,
                    (SELECT count(*) FROM agent_session_summaries s WHERE s.subject_user_id=:subject) AS summaries
                FROM agent_memory_profiles p JOIN agent_memory_subject_fences f USING (subject_user_id)
                WHERE p.subject_user_id=:subject AND p.organization_id=:org
            """),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            assert dict(row) == {"write_epoch": 1, "fence_revision": 1, "summaries": 0}
        same = await repo.update_settings(
            identity=identity,
            organization_id=org,
            expected_revision=1,
            extraction_enabled=False,
            personalization_enabled=None,
        )
        assert same.revision == 1
        updates = await asyncio.gather(
            *[
                repo.update_settings(
                    identity=identity,
                    organization_id=org,
                    expected_revision=1,
                    extraction_enabled=True,
                    personalization_enabled=None,
                ),
                repo.update_settings(
                    identity=identity,
                    organization_id=org,
                    expected_revision=1,
                    extraction_enabled=None,
                    personalization_enabled=True,
                ),
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(x, MemorySettings) for x in updates) == 1
        assert sum(isinstance(x, MemoryVersionConflictError) for x in updates) == 1
        with pytest.raises(MemoryValidationError):
            await repo.get_settings(identity=identity, organization_id="other-org")
    finally:
        async with db.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM agent_memory_source_messages WHERE subject_user_id=:subject"),
                params,
            )
            await conn.execute(
                text("DELETE FROM agent_session_summaries WHERE subject_user_id=:subject"), params
            )
            await conn.execute(
                text("DELETE FROM agent_memory_profiles WHERE subject_user_id=:subject"), params
            )
            await conn.execute(
                text("DELETE FROM agent_memory_subject_fences WHERE subject_user_id=:subject"),
                params,
            )
        await db.close()
