"""真实 PG 端到端：加密来源、异步提取、确认、正式记忆。模型以合成响应替代。"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.memory.candidate import MemoryCandidateExtractionService
from app.memory.candidate_repository import MemoryCandidateRepository
from app.memory.candidate_service import MemoryCandidateService
from app.memory.extraction_worker import MemoryExtractionWorker
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.memory.source import MemorySourceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1", reason="仅在隔离 PostgreSQL 中启用"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_candidate", [True, False])
async def test_source_to_approved_memory_or_empty_extraction(has_candidate):
    database = Database(Settings(_env_file=None))
    cipher = AesGcmPayloadCipher(b"9" * 32, "test")
    sources = MemorySourceRepository(database, cipher)
    repo = MemoryCandidateRepository(database, cipher)
    memory_service = MemoryService(MemoryRepository(database))

    class FakeModel:
        async def chat_json(self, messages):
            if not has_candidate:
                return '{"candidates": []}'
            assert messages[-1]["content"] == "我通常周末上午训练"
            return json.dumps(
                {
                    "candidates": [
                        {
                            "memory_type": "SCHEDULE_PREFERENCE",
                            "memory_key": "available_windows",
                            "slot_code": "available_windows",
                            "value": "周末上午",
                            "evidence_quote": "我通常周末上午训练",
                            "unit": None,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    candidate_service = MemoryCandidateService(
        MemoryCandidateExtractionService(FakeModel()), repo, memory_service
    )
    worker = MemoryExtractionWorker(database, cipher, candidate_service)
    suffix = uuid4().hex
    subject, org = f"e2e-user-{suffix}", f"e2e-org-{suffix}"
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
            message="我通常周末上午训练",
        )
        assert source is not None and await sources.complete(source)
        assert await worker.run_once() == 1
        async with database.engine.connect() as connection:
            status = (
                await connection.execute(
                    text(
                        "SELECT status FROM agent_memory_extraction_jobs WHERE source_message_id=:source"
                    ),
                    {"source": source.id},
                )
            ).scalar_one()
            assert status == "DONE"
        pending = await candidate_service.list_pending(identity=identity, organization_id=org)
        assert len(pending) == int(has_candidate)
        assert await memory_service.list_active(identity=identity, organization_id=org) == []
        if has_candidate:
            result = await candidate_service.decide(
                pending[0].id,
                identity=identity,
                decision="APPROVE",
                decision_request_id="user-approval",
            )
            assert result.candidate.status == "APPROVED"
            assert result.memory is not None and result.memory.content["value"] == "周末上午"
            active = await memory_service.list_active(identity=identity, organization_id=org)
            assert [memory.id for memory in active] == [result.memory.id]
            assert [
                event.event_type
                for event in await repo.list_events(pending[0].id, identity=identity)
            ] == ["CREATED", "APPROVED"]
    finally:
        async with database.engine.begin() as connection:
            for table in (
                "agent_memory_events",
                "agent_memories",
                "agent_memory_candidate_events",
                "agent_notification_outbox",
                "agent_memory_candidates",
                "agent_memory_source_messages",
                "agent_memory_profiles",
                "agent_memory_subject_fences",
            ):
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE subject_user_id=:subject"),
                    {"subject": subject},
                )
        await database.close()
