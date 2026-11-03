from types import SimpleNamespace

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.memory.models import MemoryValidationError
from app.memory.registry import SLOT_REGISTRY, normalize_identity
from app.memory.repository import (
    MemoryRepository,
    MemoryVersionConflictError,
    _check_fingerprint,
    _request_fingerprint,
)


def test_windows_are_distinct_and_unicode_is_canonicalized() -> None:
    morning = normalize_identity("SCHEDULE_PREFERENCE", "available_windows", "周末上午")
    afternoon = normalize_identity("SCHEDULE_PREFERENCE", "available_windows", "周末下午")
    assert morning != afternoon
    assert normalize_identity("TRAINING_GOAL", "goals", "Ａ  B") == normalize_identity(
        "TRAINING_GOAL", "goals", "a b"
    )


def test_single_slot_is_one_identity_and_slots_are_type_checked() -> None:
    assert SLOT_REGISTRY["verbosity"].cardinality == "SINGLE"
    assert normalize_identity(
        "COMMUNICATION_PREFERENCE", "verbosity", "简洁"
    ) == normalize_identity("COMMUNICATION_PREFERENCE", "verbosity", "详细")
    with pytest.raises(MemoryValidationError):
        normalize_identity("SCHEDULE_PREFERENCE", "verbosity", "简洁")
    with pytest.raises(MemoryValidationError):
        normalize_identity("SCHEDULE_PREFERENCE", "unknown", "晚上")


def test_unknown_scope_is_not_universal_scope() -> None:
    unknown, _ = normalize_identity("EQUIPMENT_AVAILABILITY", "available_equipment", "哑铃")
    home, _ = normalize_identity("EQUIPMENT_AVAILABILITY", "available_equipment", "哑铃", "家")
    assert unknown == "UNSPECIFIED"
    assert home != unknown


def test_idempotency_fingerprint_is_order_independent_but_rejects_changed_request() -> None:
    one = _request_fingerprint("save", {"value": "力量", "key": "style"})
    assert one == _request_fingerprint("save", {"key": "style", "value": "力量"})
    with pytest.raises(MemoryVersionConflictError):
        _check_fingerprint(
            {"request_fingerprint": one}, _request_fingerprint("save", {"value": "有氧"})
        )


@pytest.mark.asyncio
async def test_old_candidate_epoch_is_rejected_before_formal_writes() -> None:
    class Repository(MemoryRepository):
        async def lock_profile_on_connection(
            self, connection: object, *, subject_user_id: str, organization_id: str
        ) -> dict[str, int]:
            return {"write_epoch": 4}

    class Connection:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("stale candidate must not mutate database")

    identity = AgentIdentity(
        subject="u",
        organization_ids=frozenset({"org"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    with pytest.raises(MemoryVersionConflictError, match="撤销或清空"):
        await Repository(None).apply_candidate_on_connection(  # type: ignore[arg-type]
            Connection(),
            identity=identity,
            organization_id="org",
            candidate=SimpleNamespace(),
            operation_id="op",
            expected_epoch=3,
        )
