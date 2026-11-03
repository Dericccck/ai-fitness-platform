import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.memory.candidate import (
    MemoryCandidate,
    MemoryCandidateExtractionService,
    MemoryCandidateSnapshotEntry,
)
from app.memory.extraction_worker import MemoryExtractionWorker
from app.memory.models import FitnessMemory
from app.memory.reconciliation import conflict_snapshot_matches, reconcile_candidate


class Model:
    def __init__(self, value: dict) -> None:
        self.value = value
        self.calls = 0

    async def chat_json(self, messages):
        self.calls += 1
        return json.dumps(self.value, ensure_ascii=False)


def candidate(value="周末下午", quote="周末下午也可以", slot="available_windows"):
    return MemoryCandidate(
        memory_type="SCHEDULE_PREFERENCE",
        memory_key=slot,
        slot_code=slot,
        value=value,
        evidence_quote=quote,
    )


def memory(value="周末上午", *, version=3):
    now = datetime.now(UTC)
    return FitnessMemory(
        id="memory-1",
        subject_user_id="user-1",
        organization_id="org-1",
        memory_type="SCHEDULE_PREFERENCE",
        memory_key="available_windows",
        content={"value": value},
        source_type="USER_EXPLICIT",
        confidence=1,
        status="ACTIVE",
        version=version,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_controlled_extract_validates_slot_type_and_quote():
    model = Model(
        {
            "candidates": [
                {
                    "memory_type": "SCHEDULE_PREFERENCE",
                    "memory_key": "available_windows",
                    "slot_code": "available_windows",
                    "value": "周末下午",
                    "evidence_quote": "周末下午也可以",
                },
                {
                    "memory_type": "TRAINING_GOAL",
                    "memory_key": "available_windows",
                    "slot_code": "available_windows",
                    "value": "增肌",
                    "evidence_quote": "周末下午也可以",
                },
                {
                    "memory_type": "SCHEDULE_PREFERENCE",
                    "memory_key": "available_windows",
                    "slot_code": "available_windows",
                    "value": "周末上午",
                    "evidence_quote": "虚构证据",
                },
            ]
        }
    )
    result = await MemoryCandidateExtractionService(model).propose_controlled("周末下午也可以")
    assert len(result) == 1
    assert result[0].value == "周末下午"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_controlled_keeps_distinct_values_in_same_slot():
    model = Model(
        {
            "candidates": [
                {
                    "memory_type": "SCHEDULE_PREFERENCE",
                    "memory_key": "available_windows",
                    "slot_code": "available_windows",
                    "value": value,
                    "evidence_quote": value,
                }
                for value in ("周末上午", "周末下午")
            ]
        }
    )
    result = await MemoryCandidateExtractionService(model).propose_controlled(
        "周末上午和周末下午有空"
    )
    assert [item.value for item in result] == ["周末上午", "周末下午"]


@pytest.mark.asyncio
async def test_comparison_uses_authoritative_target_version_and_rejects_invented_id():
    model = Model({"action": "UPDATE", "target_memory_id": "memory-1"})
    result = await reconcile_candidate(candidate(quote="周末上午改成周末下午"), [memory()], model)
    assert (result.action, result.target_memory_id, result.expected_version) == (
        "UPDATE",
        "memory-1",
        3,
    )
    bad = await reconcile_candidate(
        candidate(), [memory()], Model({"action": "UPDATE", "target_memory_id": "other"})
    )
    assert bad.action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_value,quote",
    [
        ("周末上午和周二晚上", "以后改成下午"),
        ("周末上午", "周末下午也可以"),
        ("周末上午", "以后改成下午"),
    ],
)
async def test_schedule_update_requires_explicitly_identified_old_window(old_value, quote):
    result = await reconcile_candidate(
        candidate(value="下午", quote=quote),
        [memory(value=old_value)],
        Model({"action": "UPDATE", "target_memory_id": "memory-1"}),
    )
    assert result.action == "NEEDS_CLARIFICATION"
    assert result.target_memory_id is None


@pytest.mark.asyncio
async def test_comparison_noop_add_and_ambiguous():
    model = Model({"action": "ADD", "target_memory_id": None})
    assert (
        await reconcile_candidate(candidate(value="周末上午"), [memory()], model)
    ).action == "NOOP"
    assert model.calls == 0
    assert (await reconcile_candidate(candidate(), [], model)).action == "ADD"
    assert (await reconcile_candidate(candidate(), [memory()], model)).action == "ADD"
    assert (
        await reconcile_candidate(candidate(), [memory()], Model({"action": "UNKNOWN"}))
    ).action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
async def test_single_valued_slot_never_accepts_add_when_current_exists():
    old = memory()
    old = FitnessMemory(
        **{**old.__dict__, "memory_key": "session_duration", "content": {"value": "30分钟"}}
    )
    new = candidate(value="60分钟", quote="每次60分钟", slot="session_duration")
    result = await reconcile_candidate(new, [old], Model({"action": "ADD"}))
    assert result.action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
async def test_negation_is_never_accepted_as_independent_add():
    existing = memory(value="周末上午有空")
    new = candidate(value="周末下午没空", quote="周末下午也没空")
    result = await reconcile_candidate(new, [existing], Model({"action": "ADD"}))
    assert result.action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
async def test_unknown_same_slot_relation_does_not_add():
    result = await reconcile_candidate(
        candidate(value="下班后", quote="下班后也有空"), [memory()], Model({"action": "ADD"})
    )
    assert result.action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,profile_epoch", [(False, 3), (True, 4)])
async def test_disabled_or_revoked_source_never_reaches_extractor(enabled, profile_epoch):
    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Connection:
        def __init__(self):
            self.done = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement, params):
            sql = str(statement)
            if "SELECT * FROM agent_memory_source_messages" in sql:
                return Result({"id": "s", "write_epoch": 3})
            if "SELECT write_epoch, revision, extraction_enabled" in sql:
                return Result(
                    {"write_epoch": profile_epoch, "revision": 5, "extraction_enabled": enabled}
                )
            if "SET status='DONE'" in sql:
                self.done = True
            return Result(None)

    class NeverCallExtractor:
        async def propose_controlled(self, message):
            raise AssertionError("stale source must not be sent to the model")

    conn = Connection()
    engine = SimpleNamespace(connect=lambda: conn, begin=lambda: conn)
    worker = MemoryExtractionWorker(
        SimpleNamespace(engine=engine), None, SimpleNamespace(extractor=NeverCallExtractor())
    )
    await worker._process(
        {
            "id": "j",
            "source_message_id": "s",
            "subject_user_id": "u",
            "organization_id": "o",
            "write_epoch": 3,
            "lease_token": "t",
        }
    )
    assert conn.done


def test_snapshot_allows_unchanged_slot_and_rejects_changed_or_removed_memory():
    existing = memory()
    proposal = candidate().model_copy(
        update={
            "source_message_id": "source-1",
            "conflict_snapshot": [MemoryCandidateSnapshotEntry(memory_id=existing.id, version=3)],
        }
    )
    assert conflict_snapshot_matches(proposal, [existing], {})
    assert not conflict_snapshot_matches(proposal, [replace(existing, version=4)], {})
    assert not conflict_snapshot_matches(proposal, [], {})


def test_snapshot_accepts_proven_disjoint_add_from_same_source():
    proposal = candidate(value="周末下午").model_copy(
        update={"source_message_id": "source-1", "conflict_snapshot": []}
    )
    sibling = candidate(value="周末上午", quote="周末上午也可以").model_copy(
        update={"source_message_id": "source-1"}
    )
    saved = replace(memory(value="周末上午"), version=1)
    assert conflict_snapshot_matches(proposal, [saved], {saved.id: sibling})
    assert not conflict_snapshot_matches(proposal, [saved], {})
    other_source = sibling.model_copy(update={"source_message_id": "source-2"})
    assert not conflict_snapshot_matches(proposal, [saved], {saved.id: other_source})
    assert not conflict_snapshot_matches(proposal, [replace(saved, version=2)], {saved.id: sibling})


@pytest.mark.parametrize(
    "left,right",
    [
        ("周末早上", "周末上午"),
        ("周末上午", "周日上午"),
        ("周末上午不行", "周末下午"),
        ("上午到下午", "晚上"),
        ("周末中午", "周末下午"),
    ],
)
def test_snapshot_rejects_overlapping_negated_or_unknown_adds(left, right):
    proposal = candidate(value=left).model_copy(
        update={"source_message_id": "source-1", "conflict_snapshot": []}
    )
    sibling = candidate(value=right).model_copy(update={"source_message_id": "source-1"})
    saved = replace(memory(value=right), version=1)
    assert not conflict_snapshot_matches(proposal, [saved], {saved.id: sibling})


def test_snapshot_never_allows_other_addition_for_an_update():
    proposal = candidate(value="周末下午").model_copy(
        update={"source_message_id": "source-1", "conflict_snapshot": [], "action": "UPDATE"}
    )
    sibling = candidate(value="周末上午").model_copy(update={"source_message_id": "source-1"})
    saved = replace(memory(value="周末上午"), version=1)
    assert not conflict_snapshot_matches(proposal, [saved], {saved.id: sibling})
