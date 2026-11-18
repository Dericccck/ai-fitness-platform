import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.evaluation.task_eval.budget_store import BudgetError, DurableBudget
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.supervisor_model_probe import (
    INPUT_LIMIT,
    MESSAGES,
    OUTPUT_LIMIT,
    ProbeError,
    _OneCallGateway,
    evaluate_supervisor_model_once,
)

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def response():
    return {
        "choices": [
            {"message": {"content": "测试答案，不应出现在报告里"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 25,
            "total_tokens": 175,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 150,
        },
    }


@pytest.mark.asyncio
async def test_real_supervisor_one_mocked_request_does_not_leak(tmp_path):
    requests = []
    budget = DurableBudget(
        tmp_path / "budget.sqlite", limit_nano=500_000_000, opening_nano=200_000_000
    )

    def handler(request):
        requests.append(request)
        assert request.url == "https://api.deepseek.com/v1/chat/completions"
        body = json.loads(request.content)
        assert body["messages"] == MESSAGES
        assert body["max_tokens"] == OUTPUT_LIMIT
        assert body["thinking"] == {"type": "disabled"}
        assert "tools" not in body
        return httpx.Response(200, json=response())

    report = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
        durable_budget=budget,
    )
    assert len(requests) == 1
    assert report["component_status"] == "OBSERVED"
    assert report["task_status"] == "UNKNOWN"
    assert report["route"] == "FITNESS_COACHING"
    assert len(report["source_sha256"]["agent/supervisor.py"]) == 64
    assert report["tool_steps"] == 0
    assert report["attempts"] == 1
    assert report["reserved_cost_upper_bound"] == "0.1024"
    assert report["user_total_budget_cny"] == "0.5"
    assert report["prior_observed_reservation_cny"] == "0.2"
    assert report["projected_known_reservations_cny"] == "0.3024"
    assert report["input_tokens"] == 150
    assert "example-private-key" not in json.dumps(report, ensure_ascii=False)
    assert "测试答案" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "oversized", "invalid_usage", "redirect", "tool_call"])
async def test_model_errors_are_incomplete_and_never_retried(kind, tmp_path):
    requests = []
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)

    def handler(request):
        requests.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("sensitive response", request=request)
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * 65537)
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.invalid"})
        payload = response()
        if kind == "invalid_usage":
            payload["usage"]["prompt_tokens"] = INPUT_LIMIT + 1
        if kind == "tool_call":
            payload["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "write"}}]
        return httpx.Response(200, json=payload)

    report = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
        durable_budget=budget,
    )
    assert len(requests) == 1
    assert report["component_status"] == "INCOMPLETE"
    assert report["task_status"] == "UNKNOWN"
    assert report["attempts"] == 1
    assert "sensitive response" not in json.dumps(report)


@pytest.mark.asyncio
async def test_inputs_and_call_count_fail_closed(tmp_path):
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=response())
    )
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    gateway = _OneCallGateway("example-private-key", transport, budget)
    with pytest.raises(ProbeError):
        await gateway.chat_with_tools([{"role": "user", "content": "private data"}], tools=[])
    assert len(requests) == 0
    await gateway.chat_with_tools(MESSAGES, tools=[])
    with pytest.raises(ProbeError):
        await gateway.chat_with_tools(MESSAGES, tools=[])
    assert len(requests) == 1
    with pytest.raises(ValueError):
        await evaluate_supervisor_model_once(
            load_dataset(DATASET), credential="key", transport=transport
        )
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_previously_reserved_budget_is_checked_before_network(tmp_path):
    budget = DurableBudget(
        tmp_path / "budget.sqlite", limit_nano=150_000_000, opening_nano=50_000_000
    )
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=response())
    )
    report = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=transport,
        durable_budget=budget,
    )
    assert requests == []
    assert report["component_status"] == "INCOMPLETE"
    assert report["reason"] == "DURABLE_BUDGET_UNAVAILABLE"
    assert report["attempts"] == 0
    assert report["user_total_budget_cny"] == "0.15"
    assert report["prior_observed_reservation_cny"] == "0.05"
    assert report["projected_known_reservations_cny"] == "0.05"


@pytest.mark.asyncio
async def test_real_network_requires_persistent_budget():
    with pytest.raises(ValueError, match="persistent budget"):
        await evaluate_supervisor_model_once(
            load_dataset(DATASET),
            credential="example-private-key",
            execute=True,
            confirmed_current_pricing=True,
        )


@pytest.mark.asyncio
async def test_mock_also_requires_explicit_budget_and_never_sends_without_it():
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=response())
    )
    with pytest.raises(ValueError, match="persistent budget"):
        await evaluate_supervisor_model_once(
            load_dataset(DATASET),
            credential="example-private-key",
            execute=True,
            confirmed_current_pricing=True,
            transport=transport,
        )
    assert requests == []


@pytest.mark.asyncio
async def test_budget_reserved_before_call_and_persisted(tmp_path):
    budget = DurableBudget(
        tmp_path / "ledger.sqlite", limit_nano=10_000_000_000, opening_nano=46_080_000
    )

    def handler(request):
        assert budget.snapshot()["pending"] == 1
        return httpx.Response(200, json=response())

    report = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
        durable_budget=budget,
    )
    assert report["component_status"] == "OBSERVED"
    assert report["local_budget"]["reserved_nano"] == 148_480_000
    assert report["local_budget"]["pending"] == 0
    repeat = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
        durable_budget=budget,
    )
    assert repeat["prior_observed_reservation_cny"] == "0.14848"
    assert repeat["projected_known_reservations_cny"] == "0.25088"


@pytest.mark.asyncio
async def test_unknown_provider_usage_freezes_persistent_budget(tmp_path):
    budget = DurableBudget(
        tmp_path / "ledger.sqlite", limit_nano=10_000_000_000, opening_nano=46_080_000
    )

    def handler(request):
        raise httpx.ReadTimeout("sensitive response", request=request)

    report = await evaluate_supervisor_model_once(
        load_dataset(DATASET),
        credential="example-private-key",
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
        durable_budget=budget,
    )
    assert report["component_status"] == "INCOMPLETE"
    assert report["local_budget"]["frozen"]
    assert report["local_budget"]["pending"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["tool", "reasoning", "length", "choices", "zero"])
async def test_anomalous_response_freezes_even_with_token_counts(tmp_path, mutation):
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    payload = response()
    if mutation == "tool":
        payload["choices"][0]["message"]["server_tool_calls"] = [{"name": "unexpected"}]
    elif mutation == "reasoning":
        payload["choices"][0]["message"]["reasoning_content"] = "unexpected"
    elif mutation == "length":
        payload["choices"][0]["finish_reason"] = "length"
    elif mutation == "choices":
        payload["choices"] = []
    else:
        payload["usage"].update(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
        )
    gateway = _OneCallGateway(
        "test-key", httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), budget
    )
    with pytest.raises(ProbeError):
        await gateway.chat_with_tools(MESSAGES, tools=[])
    assert budget.snapshot()["frozen"] is True
    assert budget.snapshot()["pending"] == 0
    with pytest.raises(BudgetError):
        budget.reserve("next", 1)


@pytest.mark.asyncio
async def test_cancelled_call_freezes_persistent_budget(tmp_path):
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)

    async def cancelled(_):
        raise asyncio.CancelledError()

    gateway = _OneCallGateway("test-key", httpx.MockTransport(cancelled), budget)
    with pytest.raises(asyncio.CancelledError):
        await gateway.chat_with_tools(MESSAGES, tools=[])
    assert budget.snapshot()["frozen"] is True
    assert budget.snapshot()["pending"] == 0


@pytest.mark.asyncio
async def test_durable_reservation_tracks_actual_bound(tmp_path, monkeypatch):
    from app.evaluation.task_eval import supervisor_model_probe

    monkeypatch.setattr(supervisor_model_probe, "INPUT_LIMIT", 4095)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    gateway = _OneCallGateway(
        "test-key", httpx.MockTransport(lambda _: httpx.Response(200, json=response())), budget
    )
    await gateway.chat_with_tools(MESSAGES, tools=[])
    assert budget.snapshot()["reserved_nano"] == 102_380_000
    assert gateway.durable_reserved_nano == 102_380_000
