from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.memory.candidate import MemoryCandidate, MemoryCandidateRecord
from app.memory.candidate_service import MemoryCandidateService
from app.memory.models import FitnessMemory
from app.memory.service import MemoryService

IDENTITY = AgentIdentity(
    subject="student-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"STUDENT"}),
    issued_at=1,
    expires_at=2,
)
CANDIDATE = MemoryCandidate(
    memory_type="EQUIPMENT_AVAILABILITY",
    memory_key="available_equipment",
    value="弹力带",
)


class FakeExtractor:
    def __init__(self, candidates: tuple[MemoryCandidate, ...]) -> None:
        self.candidates = candidates

    async def propose(self, _: str) -> tuple[MemoryCandidate, ...]:
        return self.candidates


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str, str], FitnessMemory] = {}
        self.request_results: dict[str, FitnessMemory] = {}

    async def save(self, **kwargs: object) -> FitnessMemory:
        request_id = str(kwargs["source_request_id"])
        if request_id in self.request_results:
            return self.request_results[request_id]
        key = (
            str(kwargs["identity"].subject),
            str(kwargs["organization_id"]),
            str(kwargs["memory_type"]),
            str(kwargs["memory_key"]),
        )
        previous = self.items.get(key)
        now = datetime.now(UTC)
        item = FitnessMemory(
            id=previous.id if previous else "memory-1",
            subject_user_id="student-1",
            organization_id="org-1",
            memory_type=kwargs["memory_type"],  # type: ignore[arg-type]
            memory_key=str(kwargs["memory_key"]),
            content=dict(kwargs["content"]),  # type: ignore[arg-type]
            source_type="USER_EXPLICIT",
            confidence=1.0,
            status="ACTIVE",
            version=previous.version + 1 if previous else 1,
            expires_at=None,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self.items[key] = item
        self.request_results[request_id] = item
        return item

    async def list_active(self, **_: object) -> list[FitnessMemory]:
        return list(self.items.values())

    async def revoke(self, **_: object) -> FitnessMemory:
        raise AssertionError("not used by this test")

    async def expire_due(self, **_: object) -> int:
        return 0


class FakeCandidateRepository:
    def __init__(self) -> None:
        self.records: dict[str, MemoryCandidateRecord] = {}
        self.created = 0

    async def create_pending(self, **kwargs: object) -> MemoryCandidateRecord:
        candidate = kwargs["candidate"]
        record = MemoryCandidateRecord(
            id=f"candidate-{self.created + 1}",
            subject_user_id=kwargs["identity"].subject,  # type: ignore[union-attr]
            organization_id=str(kwargs["organization_id"]),
            candidate=candidate,  # type: ignore[arg-type]
            payload_hash="a" * 64,
            source_thread_id=str(kwargs["source_thread_id"]),
            source_request_id=str(kwargs["source_request_id"]),
            status="PENDING",
            expires_at=kwargs["expires_at"],  # type: ignore[arg-type]
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.records[record.id] = record
        self.created += 1
        return record

    async def list_pending(self, **_: object) -> list[MemoryCandidateRecord]:
        return [record for record in self.records.values() if record.status == "PENDING"]

    async def get_for_subject(self, candidate_id: str, **_: object) -> MemoryCandidateRecord:
        return self.records[candidate_id]

    async def decide(self, candidate_id: str, **kwargs: object) -> MemoryCandidateRecord:
        previous = self.records[candidate_id]
        decision: Literal["APPROVED", "REJECTED"] = kwargs["decision"]  # type: ignore[assignment]
        updated = MemoryCandidateRecord(
            **{
                **previous.__dict__,
                "status": decision,
                "decision_request_id": kwargs["decision_request_id"],
                "decided_at": kwargs["now"],
                "updated_at": kwargs["now"],
            }
        )
        self.records[candidate_id] = updated
        return updated


def build_service(
    candidates: tuple[MemoryCandidate, ...] = (CANDIDATE,),
) -> tuple[MemoryCandidateService, FakeCandidateRepository, FakeMemoryRepository]:
    candidate_repository = FakeCandidateRepository()
    memory_repository = FakeMemoryRepository()
    service = MemoryCandidateService(
        FakeExtractor(candidates),  # type: ignore[arg-type]
        candidate_repository,  # type: ignore[arg-type]
        MemoryService(memory_repository),  # type: ignore[arg-type]
    )
    return service, candidate_repository, memory_repository


@pytest.mark.asyncio
async def test_propose_persists_pending_and_approve_promotes_to_active_memory() -> None:
    service, candidates, memories = build_service()

    proposed = await service.propose(
        user_message="请记住我只有弹力带",
        identity=IDENTITY,
        thread_id="fitness:thread-hash",
        source_request_id="request-1",
    )

    assert proposed == (CANDIDATE,)
    assert candidates.created == 1
    assert next(iter(candidates.records.values())).status == "PENDING"
    assert memories.items == {}

    result = await service.decide(
        "candidate-1",
        identity=IDENTITY,
        decision="APPROVE",
        decision_request_id="decision-1",
    )

    assert result.candidate.status == "APPROVED"
    assert result.memory is not None
    assert result.memory.status == "ACTIVE"
    assert result.memory.content["value"] == "弹力带"

    retried = await service.decide(
        "candidate-1",
        identity=IDENTITY,
        decision="APPROVE",
        decision_request_id="decision-1",
    )
    assert retried.memory is not None
    assert retried.memory.id == result.memory.id
    assert retried.memory.version == result.memory.version


@pytest.mark.asyncio
async def test_reject_does_not_create_memory_and_multi_org_is_not_persisted() -> None:
    service, _, memories = build_service()
    await service.propose(
        user_message="请记住我喜欢弹力带",
        identity=IDENTITY,
        thread_id="fitness:thread-hash",
        source_request_id="request-2",
    )
    rejected = await service.decide(
        "candidate-1",
        identity=IDENTITY,
        decision="REJECT",
        decision_request_id="decision-2",
    )
    assert rejected.candidate.status == "REJECTED"
    assert rejected.memory is None
    assert memories.items == {}

    multi_org_identity = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1", "org-2"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    other_service, other_candidates, _ = build_service()
    await other_service.propose(
        user_message="请记住我喜欢弹力带",
        identity=multi_org_identity,
        thread_id="fitness:thread-hash",
        source_request_id="request-3",
    )
    assert other_candidates.created == 0


@pytest.mark.asyncio
async def test_expired_pending_candidate_cannot_be_approved() -> None:
    service, candidates, _ = build_service()
    await service.propose(
        user_message="请记住我喜欢弹力带",
        identity=IDENTITY,
        thread_id="fitness:thread-hash",
        source_request_id="request-4",
    )
    record = candidates.records["candidate-1"]
    candidates.records["candidate-1"] = MemoryCandidateRecord(
        **{**record.__dict__, "expires_at": datetime.now(UTC) - timedelta(minutes=1)}
    )

    with pytest.raises(RuntimeError, match="已过期"):
        await service.decide(
            "candidate-1",
            identity=IDENTITY,
            decision="APPROVE",
            decision_request_id="decision-4",
        )
