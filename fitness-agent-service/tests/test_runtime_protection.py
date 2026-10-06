import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError

from app.core.config import Settings
from app.infrastructure.model_gateway import ModelGateway, ModelResponseError, _stable_request
from app.infrastructure.model_protection import CircuitBreaker, ModelCapacityError, ModelProtection
from app.infrastructure.model_usage import collect_usage


def test_circuit_only_allows_one_half_open_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("app.infrastructure.model_protection.monotonic", lambda: clock[0])
    breaker = CircuitBreaker(2, 10)
    breaker.failure(probe=False)
    assert breaker.enter() is False
    breaker.failure(probe=False)
    with pytest.raises(ModelCapacityError):
        breaker.enter()
    clock[0] += 11
    assert breaker.enter() is True
    with pytest.raises(ModelCapacityError):
        breaker.enter()
    breaker.success()
    assert breaker.enter() is False


async def test_model_queue_is_bounded_and_cancellation_releases_slot() -> None:
    protection = ModelProtection(1, 0.02, CircuitBreaker(2, 10))
    started = asyncio.Event()

    async def blocked() -> None:
        async with protection.capacity():
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await started.wait()
    with pytest.raises(ModelCapacityError):
        async with protection.capacity():
            pass
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with protection.capacity():
        pass


async def test_retry_is_visible_and_auth_errors_are_not_retried() -> None:
    gateway = ModelGateway(Settings(_env_file=None, llm_api_key="test", llm_max_attempts=2))
    request = httpx.Request("POST", "https://model.test")
    bad = APIStatusError("failed", response=httpx.Response(503, request=request), body=None)
    good = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
    )
    gateway._llm.chat.completions.create = AsyncMock(side_effect=[bad, good])
    try:
        with collect_usage("retry") as usage:
            assert await gateway.chat([{"role": "user", "content": "test"}]) == "ok"
        assert [call.status for call in usage.calls] == ["FAILED", "SUCCEEDED"]
        assert usage.summary()["usage_complete"] is False
        unauthorized = APIStatusError(
            "no", response=httpx.Response(401, request=request), body=None
        )
        create = AsyncMock(side_effect=unauthorized)
        gateway._llm.chat.completions.create = create
        with pytest.raises(ModelResponseError):
            await gateway.chat([{"role": "user", "content": "test"}])
        create.assert_awaited_once()
        assert gateway.protection.breaker.failures == 0
    finally:
        await gateway.close()


def test_prefix_normalization_preserves_evidence_order_and_does_not_mutate_input() -> None:
    original = {
        "messages": [
            {"role": "system", "content": "固定规则"},
            {"role": "system", "content": "证据B\n证据A"},
            {"role": "user", "content": "问题"},
        ],
        "tools": [{"function": {"name": "z"}}, {"function": {"name": "a"}}],
    }
    normalized = _stable_request(original)
    assert normalized["messages"][0]["content"] == "固定规则\n\n证据B\n证据A"
    assert normalized["tools"][0]["function"]["name"] == "a"
    assert len(original["messages"]) == 3


async def test_total_deadline_includes_usage_persistence_before_provider_call() -> None:
    async def sink(usage):
        if usage.status == "STARTED":
            await asyncio.sleep(1)

    gateway = ModelGateway(Settings(_env_file=None, llm_api_key="test"), usage_sink=sink)
    gateway.logical_timeout_seconds = 0.02
    create = AsyncMock()
    gateway._llm.chat.completions.create = create
    try:
        with collect_usage("deadline") as usage, pytest.raises(ModelResponseError):
            await gateway.chat([{"role": "user", "content": "test"}])
        assert usage.calls[0].status == "UNKNOWN"
        create.assert_not_called()
        assert gateway.protection.semaphore._value == gateway.settings.llm_max_concurrency
    finally:
        await gateway.close()
