from dataclasses import dataclass

import pytest

from app.agent.training_plan_generation import (
    TrainingPlanGenerationError,
    TrainingPlanGenerationInput,
    TrainingPlanGenerationService,
)
from app.infrastructure.agent_context import AgentIdentity
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
        assert scope.subject == "coach-1"  # type: ignore[attr-defined]
        return self.result


class FakeModels:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


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
async def test_generation_rejects_non_contiguous_days_without_writing() -> None:
    broken = valid_json().replace('"day_number":2', '"day_number":3')
    service = TrainingPlanGenerationService(FakeModels([broken]), FakeRag(evidence()))

    with pytest.raises(TrainingPlanGenerationError, match="训练日编号"):
        await service.generate(request(), IDENTITY)
