import pytest

from app.memory.candidate import (
    MemoryCandidateExtractionError,
    MemoryCandidateExtractionService,
    should_attempt_controlled_extraction,
)


class FakeModels:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_controlled_extractor_pre_gates_one_off_booking_without_calling_llm() -> None:
    models = FakeModels('{"candidates": []}')
    service = MemoryCandidateExtractionService(models)  # type: ignore[arg-type]

    assert not should_attempt_controlled_extraction("星期三帮我约一节坐姿划船的课")
    assert not should_attempt_controlled_extraction("帮我查一下2026年8月营收")
    result = await service.propose_controlled("星期三帮我约一节坐姿划船的课")

    assert result == ()
    assert models.calls == []


@pytest.mark.asyncio
async def test_controlled_extractor_returns_validated_stable_candidates() -> None:
    models = FakeModels(
        '{"candidates":[{"memory_type":"SCHEDULE_PREFERENCE","memory_key":"available_windows",'
        '"slot_code":"available_windows","value":"周二晚上","unit":null,'
        '"evidence_quote":"我通常周二晚上训练","stability":"STABLE_PREFERENCE"}]}'
    )
    service = MemoryCandidateExtractionService(models)  # type: ignore[arg-type]

    result = await service.propose_controlled("请记住，我通常周二晚上训练")

    assert len(result) == 1
    assert result[0].memory_type == "SCHEDULE_PREFERENCE"
    assert result[0].slot_code == "available_windows"
    assert result[0].to_memory_tool_input("org-1")["organization_id"] == "org-1"
    assert len(models.calls) == 1


@pytest.mark.asyncio
async def test_controlled_extractor_filters_health_one_off_and_rejects_bad_json() -> None:
    health_models = FakeModels(
        '{"candidates":[{"memory_type":"TRAINING_PREFERENCE","memory_key":"exercise_preference",'
        '"slot_code":"exercise_preference","value":"膝盖疼痛","unit":null,'
        '"evidence_quote":"膝盖疼痛","stability":"STABLE_PREFERENCE"}]}'
    )
    health_service = MemoryCandidateExtractionService(health_models)  # type: ignore[arg-type]
    assert await health_service.propose_controlled("请记住我膝盖疼痛") == ()

    one_off_models = FakeModels(
        '{"candidates":[{"memory_type":"TRAINING_PREFERENCE","memory_key":"exercise_preference",'
        '"slot_code":"exercise_preference","value":"坐姿划船","unit":null,'
        '"evidence_quote":"坐姿划船","stability":"ONE_OFF_REQUEST"}]}'
    )
    one_off_service = MemoryCandidateExtractionService(one_off_models)  # type: ignore[arg-type]
    assert await one_off_service.propose_controlled("我今天临时想试一下坐姿划船") == ()

    broken_service = MemoryCandidateExtractionService(FakeModels("not-json"))  # type: ignore[arg-type]
    with pytest.raises(MemoryCandidateExtractionError):
        await broken_service.propose_controlled("请记住我喜欢弹力带")
