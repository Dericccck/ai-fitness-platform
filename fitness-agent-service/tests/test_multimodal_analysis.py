from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.agent.movement_analysis import MovementAnalysisService
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.multimodal_gateway import (
    AuthorizedMediaRef,
    MultimodalGateway,
    MultimodalOutcomeUnknown,
    MultimodalResponseError,
    MultimodalTurn,
    MultimodalUnavailable,
)
from app.rag.models import KnowledgeChunk
from app.rag.service import RagSearchResult


def configured() -> Settings:
    return Settings(
        _env_file=None,
        multimodal_enabled=True,
        multimodal_api_key="test-only-key",
        multimodal_base_url="https://model.example/compatible-mode/v1",
        multimodal_media_hosts=["assets.example"],
    )


def video() -> AuthorizedMediaRef:
    return AuthorizedMediaRef(
        "asset-1", 1, "video", "https://assets.example/a?signature=secret", 20
    )


def success(content: str = '{"ok":true}', finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


async def test_video_contract_and_usage_hide_secret() -> None:
    calls = []
    ledger = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json=success(), headers={"x-request-id": "provider-1"})

    async def write_usage(usage: Any) -> None:
        ledger.append(usage)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    gateway = MultimodalGateway(configured(), client=client, usage_sink=write_usage)
    result = await gateway.generate_json(
        system_prompt="返回JSON", user_prompt="看下动作", media=[video()]
    )
    item = calls[0]["messages"][1]["content"][0]
    assert item["type"] == "video_url" and item["fps"] == 2
    assert "fps" not in item["video_url"]
    assert result.input_tokens == 100 and result.output_tokens == 20
    assert [usage.status for usage in ledger] == ["STARTED", "SUCCEEDED"]
    assert "secret" not in repr(video()) and "test-only-key" not in repr(configured())
    await gateway.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.example/a",
        "https://other.example/a",
        "https://assets.example:444/a",
        "https://user@assets.example/a",
        "https://assets.example/a#fragment",
    ],
)
async def test_untrusted_media_never_reaches_provider(url: str) -> None:
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    gateway = MultimodalGateway(configured(), client=client)
    with pytest.raises(MultimodalResponseError):
        await gateway.generate_json(
            system_prompt="JSON", user_prompt="分析", media=[replace(video(), url=url)]
        )
    assert calls == []
    await gateway.close()


async def test_timeout_is_unknown_and_never_retried() -> None:
    calls = []
    ledger = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("network failed", request=request)

    async def save(usage: Any) -> None:
        ledger.append(usage)

    gateway = MultimodalGateway(
        configured(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        usage_sink=save,
    )
    with pytest.raises(MultimodalOutcomeUnknown):
        await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
    assert len(calls) == 1
    assert [item.status for item in ledger] == ["STARTED", "UNKNOWN"]
    await gateway.close()


async def test_provider_rejection_logs_status_without_sensitive_response() -> None:
    from structlog.testing import capture_logs

    gateway = MultimodalGateway(
        configured(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(403, text="private-provider-response")
            )
        ),
    )
    try:
        with capture_logs() as events, pytest.raises(MultimodalUnavailable):
            await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
        assert any(
            item.get("event") == "multimodal_provider_rejected" and item.get("status_code") == 403
            for item in events
        )
        assert "private-provider-response" not in str(events)
        assert "test-only-key" not in str(events) and "signature=secret" not in str(events)
    finally:
        await gateway.close()


async def test_oversized_provider_response_records_unknown_usage() -> None:
    ledger = []

    async def save(usage: Any) -> None:
        ledger.append(usage)

    gateway = MultimodalGateway(
        configured(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
            )
        ),
        usage_sink=save,
    )
    with pytest.raises(MultimodalResponseError):
        await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
    assert [item.status for item in ledger] == ["STARTED", "UNKNOWN"]
    await gateway.close()


@pytest.mark.parametrize(
    "payload",
    [success(finish_reason="length"), success("not json"), success("[]"), {"choices": []}],
)
async def test_incomplete_or_invalid_model_output_is_not_published(payload: dict[str, Any]) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    gateway = MultimodalGateway(configured(), client=client)
    with pytest.raises(MultimodalResponseError):
        await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
    await gateway.close()


async def test_disabled_is_fail_closed() -> None:
    gateway = MultimodalGateway(Settings(_env_file=None))
    with pytest.raises(MultimodalUnavailable):
        await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
    await gateway.close()


def identity() -> AgentIdentity:
    return AgentIdentity("user-1", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999)


class FakeGateway:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, **kwargs: Any) -> MultimodalTurn:
        self.calls.append(kwargs)
        return MultimodalTurn(
            json.dumps(self.responses.pop(0), ensure_ascii=False), "vision", 1, 1, None
        )


class FakeRag:
    def __init__(self, result: RagSearchResult | None = None) -> None:
        self.result = result or RagSearchResult(())
        self.scopes: list[Any] = []

    async def search(self, query: str, scope: Any) -> RagSearchResult:
        self.scopes.append(scope)
        return self.result


def report(**updates: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exercise": {"name": "坐姿划船", "identification": "IDENTIFIED"},
        "assessment": "NEEDS_ADJUSTMENT",
        "summary": "可以留意躯干稳定",
        "observations": [
            {
                "id": "o1",
                "asset_id": "asset-1",
                "description": "躯干有后仰",
                "evidence_type": "VIDEO",
                "time_range": None,
            }
        ],
        "adjustments": [
            {
                "advice": "留意躯干稳定",
                "related_observation_ids": ["o1"],
                "basis_type": "OBSERVATION",
                "citation_ids": [],
            }
        ],
        "limitations": [],
        "follow_up_question": None,
    }
    result.update(updates)
    return result


async def test_unidentified_movement_returns_clarification_without_guessing() -> None:
    gateway = FakeGateway([{"name": "", "identification": "UNCERTAIN"}])
    rag = FakeRag()
    service = MovementAnalysisService(gateway, rag, prompt_version="v1")  # type: ignore[arg-type]
    output = await service.analyze(identity=identity(), question="动作标准吗", media=[video()])
    assert output.assessment == "NEEDS_CLARIFICATION"
    assert output.source_assets[0].asset_id == "asset-1"
    assert not rag.scopes and len(gateway.calls) == 1


async def test_recognition_rag_and_final_model_receive_actual_video() -> None:
    chunk = KnowledgeChunk(
        "chunk-1",
        "doc-1",
        0,
        "训练知识",
        "internal://private",
        "训练指南",
        "TRAINING_GUIDE",
        1,
        0.9,
        {},
    )
    rag = FakeRag(RagSearchResult((chunk,)))
    result = report()
    result["adjustments"][0]["citation_ids"] = ["doc-1:0"]
    gateway = FakeGateway([{"name": "坐姿划船", "identification": "IDENTIFIED"}, result])
    service = MovementAnalysisService(gateway, rag, prompt_version="v1")  # type: ignore[arg-type]
    output = await service.analyze(identity=identity(), question="动作标准吗", media=[video()])
    assert len(gateway.calls) == 2 and all(
        call["media"][0].asset_id == "asset-1" for call in gateway.calls
    )
    assert rag.scopes[0].subject == "user-1" and rag.scopes[0].organization_ids == {"org-1"}
    assert (
        output.knowledge_status == "USED" and output.knowledge_citations[0].citation_id == "doc-1:0"
    )
    assert "internal://private" not in output.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        {"assessment": "NO_OBVIOUS_ISSUE_OBSERVED", "observations": [], "adjustments": []},
        {
            "observations": [
                {"id": "o1", "asset_id": "foreign", "description": "x", "evidence_type": "VIDEO"}
            ]
        },
        {
            "observations": [
                {
                    "id": "o1",
                    "asset_id": "asset-1",
                    "description": "x",
                    "evidence_type": "VIDEO",
                    "time_range": {"start_seconds": 1, "end_seconds": 21},
                }
            ]
        },
        {
            "adjustments": [
                {
                    "advice": "x",
                    "related_observation_ids": ["o1"],
                    "basis_type": "OBSERVATION",
                    "citation_ids": ["fabricated"],
                }
            ]
        },
    ],
)
async def test_report_rejects_fabricated_sources_and_times(change: dict[str, Any]) -> None:
    gateway = FakeGateway([report(**change)])
    service = MovementAnalysisService(gateway, FakeRag(), prompt_version="v1")  # type: ignore[arg-type]
    with pytest.raises(MultimodalResponseError):
        await service.analyze(
            identity=identity(), question="分析", media=[video()], exercise_hint="坐姿划船"
        )


def test_configuration_requires_private_media_allowlist() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            multimodal_enabled=True,
            multimodal_api_key="key",
            multimodal_base_url="https://model.example/compatible-mode/v1",
        )


async def test_provider_total_timeout_is_unknown_even_when_transport_does_not_timeout() -> None:
    import asyncio

    calls = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=success())

    settings = configured().model_copy(update={"multimodal_timeout_seconds": 0.03})
    gateway = MultimodalGateway(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(MultimodalOutcomeUnknown):
        await gateway.generate_json(system_prompt="JSON", user_prompt="分析", media=[video()])
    assert len(calls) == 1
    await gateway.close()


async def test_context_uses_only_authorized_communication_memory_and_bounded_history():
    from datetime import UTC, datetime

    from app.memory.models import FitnessMemory

    now = datetime.now(UTC)
    preference = FitnessMemory(
        "memory-1",
        "user-1",
        "org-1",
        "COMMUNICATION_PREFERENCE",
        "tone",
        {"value": "回答简短"},
        "USER_EXPLICIT",
        1.0,
        "ACTIVE",
        1,
        None,
        now,
        now,
    )
    training = replace(
        preference, id="memory-2", memory_type="TRAINING_GOAL", content={"value": "私人的目标"}
    )

    class Memory:
        async def list_personalization(self, *, identity, organization_id):
            assert identity.subject == "user-1" and organization_id == "org-1"
            return [preference, training]

    gateway = FakeGateway([report()])
    service = MovementAnalysisService(
        gateway, FakeRag(), prompt_version="v1", memory_service=Memory()
    )
    await service.analyze(
        identity=identity(),
        question="还有什么？",
        media=[video()],
        exercise_hint="坐姿划船",
        history=[
            {"role": "user", "content": "第一次问题"},
            {"role": "assistant", "content": "之前的回答"},
        ],
    )
    prompt = gateway.calls[0]["user_prompt"]
    assert "第一次问题" in prompt and "之前的回答" in prompt and "回答简短" in prompt
    assert "私人的目标" not in prompt
