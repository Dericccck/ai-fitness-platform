import pytest

from app.memory.candidate import (
    MemoryCandidate,
    MemoryCandidateExtractionError,
    MemoryCandidateExtractionService,
    build_candidate_context,
)


class FakeModels:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_candidate_extractor_skips_non_memory_messages_without_calling_llm() -> None:
    models = FakeModels('{"candidates": []}')
    service = MemoryCandidateExtractionService(models)  # type: ignore[arg-type]

    result = await service.propose("今天帮我解释一下热身的作用")

    assert result == ()
    assert models.calls == []


@pytest.mark.asyncio
async def test_candidate_extractor_returns_validated_read_only_candidates() -> None:
    models = FakeModels(
        '{"candidates":[{"memory_type":"SCHEDULE_PREFERENCE",'
        '"memory_key":"training_time","value":"周二晚上","unit":null}]}'
    )
    service = MemoryCandidateExtractionService(models)  # type: ignore[arg-type]

    result = await service.propose("请记住，我通常周二晚上训练")

    assert len(result) == 1
    assert result[0].memory_type == "SCHEDULE_PREFERENCE"
    assert result[0].to_memory_tool_input("org-1")["organization_id"] == "org-1"
    assert len(models.calls) == 1


@pytest.mark.asyncio
async def test_candidate_extractor_filters_health_content_and_rejects_bad_json() -> None:
    health_models = FakeModels(
        '{"candidates":[{"memory_type":"TRAINING_PREFERENCE",'
        '"memory_key":"constraint","value":"膝盖疼痛","unit":null}]}'
    )
    health_service = MemoryCandidateExtractionService(health_models)  # type: ignore[arg-type]
    assert await health_service.propose("请记住我膝盖疼痛") == ()

    broken_service = MemoryCandidateExtractionService(FakeModels("not-json"))  # type: ignore[arg-type]
    with pytest.raises(MemoryCandidateExtractionError):
        await broken_service.propose("请记住我喜欢弹力带")


def test_candidate_context_explicitly_marks_candidates_as_unconfirmed() -> None:
    candidate = MemoryCandidate(
        memory_type="EQUIPMENT_AVAILABILITY",
        memory_key="available_equipment",
        value="弹力带",
    )

    context = build_candidate_context((candidate,), frozenset({"org-1"}))

    assert "不是已保存事实" in context
    assert "fitness.memory.save.v1" in context
    assert "弹力带" in context
