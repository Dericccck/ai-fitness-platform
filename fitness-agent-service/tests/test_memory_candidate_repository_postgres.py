"""需要显式开启的 Memory 候选 PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.candidate import MemoryCandidate
from app.memory.candidate_repository import MemoryCandidateNotFound, MemoryCandidateRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_candidate_repository_encrypts_deduplicates_and_scopes_decisions() -> None:
    database = Database(Settings(_env_file=None))
    repository = MemoryCandidateRepository(
        database,
        AesGcmPayloadCipher(key=b"1" * 32, key_version="test-v1"),
    )
    candidate_id = str(uuid4())
    identity = AgentIdentity(
        subject=f"candidate-test-{candidate_id}",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    candidate = MemoryCandidate(
        memory_type="TRAINING_PREFERENCE",
        memory_key="preferred_style",
        value="自重训练",
    )
    try:
        first = await repository.create_pending(
            identity=identity,
            organization_id="org-1",
            candidate=candidate,
            source_thread_id="fitness:thread-hash",
            source_request_id="request-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        duplicate = await repository.create_pending(
            identity=identity,
            organization_id="org-1",
            candidate=candidate,
            source_thread_id="fitness:thread-hash-2",
            source_request_id="request-2",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert duplicate.id == first.id
        assert duplicate.candidate.value == "自重训练"

        pending = await repository.list_pending(
            identity=identity,
            organization_id="org-1",
            limit=10,
        )
        assert [record.id for record in pending] == [first.id]

        other_scope = AgentIdentity(
            subject=identity.subject,
            organization_ids=frozenset({"org-2"}),
            roles=identity.roles,
            issued_at=1,
            expires_at=2,
        )
        with pytest.raises(MemoryCandidateNotFound):
            await repository.get_for_subject(first.id, identity=other_scope)

        decided = await repository.decide(
            first.id,
            identity=identity,
            decision="APPROVED",
            decision_request_id="decision-1",
            now=datetime.now(UTC),
        )
        assert decided.status == "APPROVED"
        assert decided.decision_request_id == "decision-1"
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_memory_candidates WHERE subject_user_id = :subject"),
                {"subject": identity.subject},
            )
        await database.close()
