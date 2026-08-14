from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.memory.models import FitnessMemory, MemoryValidationError
from app.memory.service import MemoryService

IDENTITY = AgentIdentity(
    subject="student-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"STUDENT"}),
    issued_at=1,
    expires_at=2,
)


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str, str], FitnessMemory] = {}
        self.request_results: dict[str, FitnessMemory] = {}
        self.next_id = 1

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
        version = previous.version + 1 if previous else 1
        now = datetime.now(UTC)
        item = FitnessMemory(
            id=previous.id if previous else f"memory-{self.next_id}",
            subject_user_id="student-1",
            organization_id="org-1",
            memory_type=kwargs["memory_type"],  # type: ignore[arg-type]
            memory_key=str(kwargs["memory_key"]),
            content=dict(kwargs["content"]),  # type: ignore[arg-type]
            source_type="USER_EXPLICIT",
            confidence=1.0,
            status="ACTIVE",
            version=version,
            expires_at=kwargs["expires_at"],  # type: ignore[arg-type]
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self.items[key] = item
        self.request_results[request_id] = item
        self.next_id += 1
        return item

    async def list_active(self, **_: object) -> list[FitnessMemory]:
        return [item for item in self.items.values() if item.status == "ACTIVE"]

    async def revoke(self, **kwargs: object) -> FitnessMemory:
        for key, item in self.items.items():
            if item.id == kwargs["memory_id"]:
                if item.version != kwargs["expected_version"]:
                    raise RuntimeError("version changed")
                revoked = FitnessMemory(
                    **{**item.__dict__, "status": "REVOKED", "version": item.version + 1}
                )
                self.items[key] = revoked
                return revoked
        raise RuntimeError("not found")

    async def expire_due(self, **_: object) -> int:
        return 0


@pytest.mark.asyncio
async def test_memory_save_is_structured_and_same_key_correction_increments_version() -> None:
    service = MemoryService(FakeMemoryRepository())  # type: ignore[arg-type]

    first = await service.save(
        identity=IDENTITY,
        organization_id="org-1",
        memory_type="TRAINING_PREFERENCE",
        memory_key="preferred_style",
        value="力量训练",
        unit=None,
        expires_at=None,
        source_request_id="request-1",
    )
    corrected = await service.save(
        identity=IDENTITY,
        organization_id="org-1",
        memory_type="TRAINING_PREFERENCE",
        memory_key="preferred_style",
        value="自重训练",
        unit=None,
        expires_at=None,
        source_request_id="request-2",
    )

    assert first.version == 1
    assert corrected.id == first.id
    assert corrected.version == 2
    assert corrected.content["value"] == "自重训练"

    retried = await service.save(
        identity=IDENTITY,
        organization_id="org-1",
        memory_type="TRAINING_PREFERENCE",
        memory_key="preferred_style",
        value="自重训练",
        unit=None,
        expires_at=None,
        source_request_id="request-2",
    )
    assert retried.id == corrected.id
    assert retried.version == corrected.version


@pytest.mark.asyncio
async def test_memory_rejects_out_of_scope_and_health_diagnosis_content() -> None:
    service = MemoryService(FakeMemoryRepository())  # type: ignore[arg-type]

    with pytest.raises(MemoryValidationError, match="outside signed identity scope"):
        await service.save(
            identity=IDENTITY,
            organization_id="org-2",
            memory_type="TRAINING_GOAL",
            memory_key="goal",
            value="增肌",
            unit=None,
            expires_at=None,
            source_request_id="request-1",
        )

    with pytest.raises(MemoryValidationError, match="not stored"):
        await service.save(
            identity=IDENTITY,
            organization_id="org-1",
            memory_type="TRAINING_GOAL",
            memory_key="goal",
            value="医生诊断的疾病",
            unit=None,
            expires_at=None,
            source_request_id="request-2",
        )


@pytest.mark.asyncio
async def test_memory_expiry_must_be_in_the_future_and_revoke_is_version_bound() -> None:
    repository = FakeMemoryRepository()
    service = MemoryService(repository)  # type: ignore[arg-type]
    with pytest.raises(MemoryValidationError, match="expiry"):
        await service.save(
            identity=IDENTITY,
            organization_id="org-1",
            memory_type="SCHEDULE_PREFERENCE",
            memory_key="training_time",
            value="周二晚上",
            unit=None,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            source_request_id="request-1",
        )

    item = await service.save(
        identity=IDENTITY,
        organization_id="org-1",
        memory_type="SCHEDULE_PREFERENCE",
        memory_key="training_time",
        value="周二晚上",
        unit=None,
        expires_at=None,
        source_request_id="request-2",
    )
    revoked = await service.revoke(
        identity=IDENTITY,
        organization_id="org-1",
        memory_id=item.id,
        expected_version=item.version,
    )
    assert revoked.status == "REVOKED"
