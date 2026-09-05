from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.agent.fitness_tools import CreateTrainingDraftToolInput, _create_training_draft_payload
from app.agent.training_plan_generation import (
    TrainingPlanGenerationError,
    TrainingPlanGenerationInput,
    TrainingPlanGenerationService,
)
from app.infrastructure.agent_context import AgentIdentity
from app.memory.models import FitnessMemory
from app.rag.models import KnowledgeChunk
from app.rag.service import RagSearchResult

IDENTITY = AgentIdentity(
    subject="coach-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"COACH"}),
    issued_at=1,
    expires_at=2,
)


def request() -> TrainingPlanGenerationInput:
    return TrainingPlanGenerationInput(
        organization_id="org-1",
        student_id="student-1",
        coach_id="coach-1",
        goal_type="力量",
        training_days=2,
        level="初级",
        session_minutes=45,
        equipment=["弹力带"],
    )


def evidence() -> RagSearchResult:
    return RagSearchResult(
        (
            KnowledgeChunk(
                id="chunk-1",
                document_id="doc-1",
                chunk_index=1,
                content="力量训练应循序渐进，并包含热身和恢复安排。",
                source_uri="knowledge://fitness/training/guide",
                title="训练指南",
                document_type="TRAINING_GUIDE",
                version=1,
                similarity=0.9,
                metadata={},
            ),
        )
    )


@dataclass
class FakeRag:
    result: RagSearchResult

    async def search(self, query: str, scope: object) -> RagSearchResult:
        assert "力量" in query
        assert scope.subject in {"coach-1", "student-1"}  # type: ignore[attr-defined]
        return self.result


class FakeModels:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(
        self, messages: list[dict[str, str]], *, max_output_tokens: int | None = None
    ) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


@dataclass
class FakeMemory:
    memories: list[FitnessMemory]

    async def list_active(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> list[FitnessMemory]:
        assert identity.subject in {"coach-1", "student-1"}
        assert organization_id == "org-1"
        return self.memories


@dataclass
class FakeStudentContext:
    memories: list[FitnessMemory]
    calls: list[tuple[str, str, str]]

    async def read_training_context(
        self, *, actor_id: str, student_id: str, organization_id: str
    ) -> list[FitnessMemory]:
        self.calls.append((actor_id, student_id, organization_id))
        return self.memories


def valid_json() -> str:
    return (
        '{"title":"弹力带力量入门","goal_type":"力量","days":['
        '{"day_number":1,"title":"全身力量","scheduled_date":null,"items":['
        '{"exercise_name":"弹力带深蹲","sort_order":1,"sets":3,"reps":"8-10",'
        '"rest_seconds":90,"target_weight_kg":null,"target_rpe":6,"notes":null}]},'
        '{"day_number":2,"title":"上肢力量","scheduled_date":null,"items":['
        '{"exercise_name":"弹力带划船","sort_order":1,"sets":3,"reps":"10-12",'
        '"rest_seconds":90,"target_weight_kg":null,"target_rpe":6,"notes":null}]}'
        "]}"
    )


def test_training_draft_payload_uses_java_gateway_field_names() -> None:
    """确认摘要和真实 Gateway 请求必须共享 camelCase 跨服务契约。"""

    typed = CreateTrainingDraftToolInput.model_validate(
        {
            "organization_id": "org-1",
            "student_id": "student-1",
            "coach_id": "coach-1",
            "title": "力量入门",
            "goal_type": "力量",
            "days": [
                {
                    "day_number": 1,
                    "title": "下肢",
                    "scheduled_date": None,
                    "items": [
                        {
                            "exercise_name": "徒手深蹲",
                            "sort_order": 1,
                            "sets": 3,
                            "reps": "10-12",
                            "rest_seconds": 60,
                            "target_weight_kg": None,
                            "target_rpe": None,
                            "notes": "保持稳定",
                        }
                    ],
                }
            ],
        }
    )

    payload = _create_training_draft_payload(typed)

    assert payload["organizationId"] == "org-1"
    assert payload["studentId"] == "student-1"
    assert payload["coachId"] == "coach-1"
    assert payload["goalType"] == "力量"
    assert payload["days"][0]["dayNumber"] == 1  # type: ignore[index]
    assert payload["days"][0]["items"][0]["exerciseName"] == "徒手深蹲"  # type: ignore[index]
    assert "organization_id" not in payload


@pytest.mark.asyncio
async def test_generation_returns_structured_preview_and_citations() -> None:
    service = TrainingPlanGenerationService(FakeModels([valid_json()]), FakeRag(evidence()))

    result = await service.generate(request(), IDENTITY)

    assert result["status"] == "DRAFT_PREVIEW"
    assert result["requires_confirmation"] is True
    assert result["requires_coach_review"] is True
    payload = result["payload"]
    assert isinstance(payload, dict)
    assert payload["organization_id"] == "org-1"
    assert len(payload["days"]) == 2
    assert result["citations"][0]["source_uri"] == "knowledge://fitness/training/guide"


@pytest.mark.asyncio
async def test_generation_repairs_one_invalid_model_response() -> None:
    models = FakeModels(['{"title":"缺字段"}', valid_json()])
    service = TrainingPlanGenerationService(models, FakeRag(evidence()))

    result = await service.generate(request(), IDENTITY)

    assert result["status"] == "DRAFT_PREVIEW"
    assert len(models.calls) == 2
    assert "未通过程序校验" in models.calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_generation_rejects_empty_authorized_knowledge() -> None:
    service = TrainingPlanGenerationService(
        FakeModels([valid_json()]), FakeRag(RagSearchResult(()))
    )

    with pytest.raises(TrainingPlanGenerationError, match="没有检索到"):
        await service.generate(request(), IDENTITY)


@pytest.mark.asyncio
async def test_generation_does_not_include_actor_memory_for_student_plan() -> None:
    now = datetime.now(UTC)
    memory = FitnessMemory(
        id="memory-1",
        subject_user_id="coach-1",
        organization_id="org-1",
        memory_type="EQUIPMENT_AVAILABILITY",
        memory_key="available_equipment",
        content={"key": "available_equipment", "value": "弹力带"},
        source_type="USER_EXPLICIT",
        confidence=1.0,
        status="ACTIVE",
        version=1,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    models = FakeModels([valid_json()])
    service = TrainingPlanGenerationService(
        models,
        FakeRag(evidence()),
        memory_service=FakeMemory([memory]),  # type: ignore[arg-type]
    )

    await service.generate(request(), IDENTITY)

    assert "available_equipment" not in models.calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_generation_reads_memory_only_for_student_actor() -> None:
    now = datetime.now(UTC)
    memory = FitnessMemory(
        id="memory-1",
        subject_user_id="student-1",
        organization_id="org-1",
        memory_type="EQUIPMENT_AVAILABILITY",
        memory_key="available_equipment",
        content={"key": "available_equipment", "value": "弹力带"},
        source_type="USER_EXPLICIT",
        confidence=1.0,
        status="ACTIVE",
        version=1,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    models = FakeModels([valid_json()])
    service = TrainingPlanGenerationService(
        models,
        FakeRag(evidence()),
        memory_service=FakeMemory([memory]),  # type: ignore[arg-type]
    )
    student_identity = AgentIdentity(
        subject="student-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )

    await service.generate(request().model_copy(update={"student_id": "student-1", "coach_id": "coach-1"}), student_identity)

    assert "available_equipment" in models.calls[0][-1]["content"]
    assert "弹力带" in models.calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_generation_uses_controlled_student_context_reader() -> None:
    now = datetime.now(UTC)
    memory = FitnessMemory(
        id="student-memory-1",
        subject_user_id="student-1",
        organization_id="org-1",
        memory_type="TRAINING_GOAL",
        memory_key="goal",
        content={"key": "goal", "value": "减脂"},
        source_type="USER_EXPLICIT",
        confidence=1.0,
        status="ACTIVE",
        version=3,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    reader = FakeStudentContext([memory], [])
    models = FakeModels([valid_json()])
    service = TrainingPlanGenerationService(
        models, FakeRag(evidence()), student_context_reader=reader  # type: ignore[arg-type]
    )

    result = await service.generate(request(), IDENTITY)

    assert reader.calls == [("coach-1", "student-1", "org-1")]
    assert result["context_sources"][0]["version"] == 3


@pytest.mark.asyncio
async def test_generation_rejects_non_contiguous_days_without_writing() -> None:
    broken = valid_json().replace('"day_number":2', '"day_number":3')
    service = TrainingPlanGenerationService(FakeModels([broken]), FakeRag(evidence()))

    with pytest.raises(TrainingPlanGenerationError, match="训练日编号"):
        await service.generate(request(), IDENTITY)
