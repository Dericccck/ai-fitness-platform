from __future__ import annotations

from time import perf_counter
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletionChunk

from app.core.config import Settings
from app.infrastructure.model_gateway import ModelGateway, ModelResponseError, _bounded_request


class FakeStream:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return ChatCompletionChunk.model_validate(self.chunks.pop(0))

    async def close(self):
        self.closed = True


def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> dict:
    return {
        "id": "test",
        "created": 1,
        "model": "test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": usage,
    }


async def test_sdk_stream_reassembles_tool_arguments_without_exposing_them(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        "app.infrastructure.model_gateway.emit_event", lambda *args: events.append(args)
    )
    stream = FakeStream(
        [
            chunk(
                {
                    "content": "内部选择",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "booking", "arguments": '{"课程":'},
                        }
                    ],
                }
            ),
            chunk(
                {"tool_calls": [{"index": 0, "function": {"arguments": '"力量"}'}}]},
                "tool_calls",
                {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            ),
        ]
    )
    model = ModelGateway(Settings(_env_file=None))
    model._llm.chat.completions.create = AsyncMock(return_value=stream)
    try:
        response, _ = await model._stream_completion(
            {"tools": [{"type": "function"}]}, started=perf_counter()
        )
        assert response.choices[0].message.tool_calls[0].function.arguments == '{"课程":"力量"}'
        assert response.usage.prompt_tokens == 20 and stream.closed
        assert events == []
    finally:
        await model.close()


async def test_sdk_stream_filters_fragmented_marker_and_rejects_truncation(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        "app.infrastructure.model_gateway.emit_event",
        lambda event, data: events.append(data["text"]),
    )
    model = ModelGateway(Settings(_env_file=None))
    stream = FakeStream([chunk({"content": "安全<thi"}), chunk({"content": "nk>隐私"}, "stop")])
    model._llm.chat.completions.create = AsyncMock(return_value=stream)
    try:
        await model._stream_completion({}, started=perf_counter())
        assert "".join(events) == "安全" and stream.closed
        truncated = FakeStream([chunk({"content": "未完成"}, "length")])
        model._llm.chat.completions.create = AsyncMock(return_value=truncated)
        with pytest.raises(ModelResponseError):
            await model._stream_completion({}, started=perf_counter())
        assert truncated.closed
    finally:
        await model.close()


def test_context_budget_removes_whole_old_turn_and_preserves_tool_pairs() -> None:
    messages = [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "old"},
        {"role": "user", "content": "当前问题"},
    ]
    bounded = _bounded_request({"messages": messages}, max_chars=500)
    assert [m["role"] for m in bounded["messages"]] == ["system", "user"]
    assert len(messages) == 5
    with pytest.raises(ModelResponseError):
        _bounded_request({"messages": [{"role": "user", "content": "x" * 1000}]}, max_chars=100)
