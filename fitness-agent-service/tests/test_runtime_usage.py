from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.model_usage import ModelUsage, collect_usage, estimate_cost, extract_usage


def settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="test",
        llm_model="test-model",
        llm_pricing_model="test-model",
        llm_pricing_version="test-price-v1",
        llm_input_price_per_million=Decimal(2),
        llm_cached_input_price_per_million=Decimal("0.2"),
        llm_output_price_per_million=Decimal(4),
    )


def test_provider_cache_usage_and_decimal_cost_are_not_double_counted() -> None:
    counts = extract_usage(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_cache_hit_tokens=80,
            prompt_cache_miss_tokens=20,
        )
    )
    assert counts["cache_hit_tokens"] == 80
    assert estimate_cost(settings(), counts) == "0.000136"
    assert (
        estimate_cost(settings().model_copy(update={"llm_pricing_model": "other"}), counts) is None
    )


def test_missing_usage_or_cache_details_remain_unknown() -> None:
    assert all(value is None for value in extract_usage(None).values())
    counts = extract_usage(SimpleNamespace(prompt_tokens=100, completion_tokens=20))
    assert estimate_cost(settings(), counts) is None
    assert (
        extract_usage(
            SimpleNamespace(prompt_tokens=2, completion_tokens=0, prompt_cache_hit_tokens=5)
        )["cache_hit_tokens"]
        is None
    )


def test_standard_cached_token_details_are_supported() -> None:
    counts = extract_usage(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
        )
    )
    assert counts["cache_miss_tokens"] == 60


async def test_usage_is_per_request_and_ledger_tracks_one_actual_attempt() -> None:
    sink = AsyncMock()
    gateway = ModelGateway(settings(), usage_sink=sink)
    gateway._llm.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="回答"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
    )
    try:
        with collect_usage("request-a", run_id="run-a") as first:
            await gateway.chat([{"role": "user", "content": "问题"}])
        with collect_usage("request-b") as second:
            await gateway.chat([{"role": "user", "content": "第二问"}])
        assert first.summary()["input_tokens"] == second.summary()["input_tokens"] == 10
        assert first.calls[0].run_id == "run-a"
        assert second.calls[0].run_id is None
        assert [call.args[0].status for call in sink.await_args_list] == [
            "STARTED",
            "SUCCEEDED",
            "STARTED",
            "SUCCEEDED",
        ]
        assert sink.await_args_list[0].args[0].call_id == sink.await_args_list[1].args[0].call_id
    finally:
        await gateway.close()


def test_unknown_attempt_does_not_report_zero_or_complete_billing() -> None:
    from app.infrastructure.model_usage import record_usage

    with collect_usage("request") as usage:
        call = ModelUsage(
            "a",
            "request",
            None,
            "test",
            "provider",
            "chat",
            1,
            "SUCCEEDED",
            "p",
            input_tokens=10,
            output_tokens=2,
            estimated_cost="0.1",
            currency="CNY",
        )
        record_usage(call)
        record_usage(
            replace(
                call,
                call_id="b",
                status="FAILED",
                input_tokens=None,
                output_tokens=None,
                estimated_cost=None,
            )
        )
    assert usage.summary()["usage_complete"] is False
    assert usage.summary()["input_tokens"] is None
    assert usage.summary()["known_input_tokens"] == 10
    assert usage.summary()["estimated_cost"] is None


async def test_no_provider_call_when_usage_reservation_fails() -> None:
    gateway = ModelGateway(settings(), usage_sink=AsyncMock(side_effect=RuntimeError("db failed")))
    create = AsyncMock()
    gateway._llm.chat.completions.create = create
    try:
        with pytest.raises(RuntimeError, match="用量记录"):
            await gateway.chat([{"role": "user", "content": "测试"}])
        create.assert_not_awaited()
    finally:
        await gateway.close()
