from datetime import UTC, datetime, timedelta

import pytest

from app.memory.context import MemoryContextBuilder
from app.memory.models import FitnessMemory, MemoryValidationError

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def memory(
    id: str, typ: str, *, owner: str = "student", org: str = "gym", **kwargs: object
) -> FitnessMemory:
    values = {
        "id": id,
        "subject_user_id": owner,
        "organization_id": org,
        "memory_type": typ,
        "memory_key": "test",
        "content": {"key": "test", "value": id},
        "source_type": "USER_EXPLICIT",
        "confidence": 1.0,
        "status": "ACTIVE",
        "version": 1,
        "expires_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(kwargs)
    return FitnessMemory(**values)  # type: ignore[arg-type]


def build(items: list[FitnessMemory], **kwargs: object):
    args = {
        "actor_id": "student",
        "subject_id": "student",
        "organization_id": "gym",
        "purpose": "training_plan",
        "now": NOW,
    }
    args.update(kwargs)
    return MemoryContextBuilder().build(items, **args)  # type: ignore[arg-type]


def test_multiple_preferences_coexist_and_injected_references_are_versions():
    items = [memory("squat", "TRAINING_PREFERENCE"), memory("row", "TRAINING_PREFERENCE")]
    result = build(items)
    assert [x.id for x in result.memories] == ["squat", "row"]
    assert result.references() == (
        {"id": "squat", "version": 1, "source_type": "USER_EXPLICIT"},
        {"id": "row", "version": 1, "source_type": "USER_EXPLICIT"},
    )


def test_expiry_revoke_and_redaction_filtered_at_read_time():
    items = [
        memory("expired", "TRAINING_GOAL", expires_at=NOW),
        memory("revoked", "TRAINING_GOAL", status="REVOKED"),
        memory("redacted", "TRAINING_GOAL", content_redacted=True),
        memory("active", "TRAINING_GOAL", expires_at=NOW + timedelta(seconds=1)),
    ]
    assert [m.id for m in build(items).memories] == ["active"]


def test_unauthorized_subject_and_organization_fail_closed():
    with pytest.raises(MemoryValidationError):
        build([memory("other", "TRAINING_GOAL", owner="other")])
    with pytest.raises(MemoryValidationError):
        build([memory("other_org", "TRAINING_GOAL", org="other")])
    with pytest.raises(MemoryValidationError):
        build([], actor_id="coach", delegated_authorized=False)


def test_delegation_excludes_student_communication_preference():
    items = [memory("goal", "TRAINING_GOAL"), memory("style", "COMMUNICATION_PREFERENCE")]
    assert [m.id for m in build(items, actor_id="coach", delegated_authorized=True).memories] == [
        "goal"
    ]
    with pytest.raises(MemoryValidationError):
        build(items, actor_id="coach", delegated_authorized=True, purpose="conversation")


def test_conversation_defaults_to_communication_and_relevant_types_opt_in():
    items = [memory("goal", "TRAINING_GOAL"), memory("style", "COMMUNICATION_PREFERENCE")]
    assert [m.id for m in build(items, purpose="conversation").memories] == ["style"]
    assert [
        m.id
        for m in build(
            items, purpose="conversation", relevant_types=frozenset({"TRAINING_GOAL"})
        ).memories
    ] == ["goal", "style"]
    with pytest.raises(MemoryValidationError):
        build(items, purpose="conversation", relevant_types=frozenset({"UNRECOGNIZED"}))


def test_conditioned_scope_is_only_used_when_caller_supplies_matching_scope():
    home = memory("home-dumbbell", "EQUIPMENT_AVAILABILITY", scope_key="scope-home")
    gym = memory("gym-barbell", "EQUIPMENT_AVAILABILITY", scope_key="scope-gym")
    assert not build([home, gym]).memories
    selected = build([home, gym], applicable_scopes=frozenset({"scope-home"}))
    assert [item.id for item in selected.memories] == ["home-dumbbell"]


def test_context_relevant_ids_and_slots_limit_same_type_in_conversation():
    items = [
        memory("morning", "SCHEDULE_PREFERENCE", slot_code="available_windows"),
        memory("evening", "SCHEDULE_PREFERENCE", slot_code="available_windows"),
        memory("duration", "SCHEDULE_PREFERENCE", slot_code="session_duration"),
        memory("style", "COMMUNICATION_PREFERENCE", slot_code="verbosity"),
    ]
    result = build(
        items,
        purpose="conversation",
        relevant_types=frozenset({"SCHEDULE_PREFERENCE"}),
        relevant_memory_ids=frozenset({"morning", "duration"}),
        relevant_slots=frozenset({"available_windows"}),
    )
    assert [m.id for m in result.memories] == ["morning", "style"]
    assert result.uncertain_scope_ids == ("morning", "style")


def test_budget_prioritizes_communication_and_training_never_truncates_silently():
    items = [memory("long-goal", "TRAINING_GOAL"), memory("style", "COMMUNICATION_PREFERENCE")]
    length = len(items[1].to_prompt_line()) + 1
    result = build(
        items,
        purpose="conversation",
        relevant_types=frozenset({"TRAINING_GOAL"}),
        max_characters=length,
    )
    assert [m.id for m in result.memories] == ["style"]
    assert result.excluded_budget_ids == ("long-goal",)
    with pytest.raises(MemoryValidationError, match="预算"):
        build(items, max_characters=length)


def test_unmatched_scope_is_recorded_as_unresolved_not_falsely_applied():
    item = memory("home", "EQUIPMENT_AVAILABILITY", scope_key="scope-home")
    result = build([item])
    assert result.memories == ()
    assert result.excluded_scope_ids == ("home",)
