import json
from types import SimpleNamespace

import httpx
import pytest

from app.evaluation.task_eval.model_safety import ModelPreflightConfig
from app.evaluation.task_eval.model_smoke import PROMPT, connectivity_once, smoke_once


def config(**changes):
    data = {
        "endpoint": "https://api.deepseek.com/v1",
        "allowed_hosts": ["api.deepseek.com"],
        "model": "test-model",
        "environment_id": "eval-isolated",
        "isolation_evidence_ref": "ticket:iso",
        "authorization_ref": "ticket:auth",
        "provider_spend_limit_ref": "ticket:limit",
        "credential_env": "FITNESS_EVAL_MODEL_API_KEY",
        "max_requests": 1,
        "max_input_tokens": 512,
        "max_output_tokens": 64,
        "max_cost": "1",
        "input_cost_per_million": "1",
        "output_cost_per_million": "2",
        "currency": "USD",
        "pricing_version": "test-pricing",
        "data_class": "synthetic",
    }
    return ModelPreflightConfig.model_validate(data | changes)


def dataset(message=PROMPT):
    c = SimpleNamespace(
        case_id="ROUT-001",
        split="dev",
        source=SimpleNamespace(type="synthetic"),
        review=SimpleNamespace(status="draft"),
        steps=[SimpleNamespace(type="chat", message=message)],
    )
    return SimpleNamespace(cases=[c], sha256="hash", dataset_version="v1")


def test_one_synthetic_call_with_usage_and_no_output_or_secret_leaks():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "api.deepseek.com"
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        assert json.loads(request.content)["max_tokens"] == 64
        assert json.loads(request.content)["messages"][0]["content"] == PROMPT
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "private-test-answer"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 20,
                },
            },
        )

    result = smoke_once(
        dataset(),
        config(),
        credential="private-test-key",
        execute=True,
        confirmed_external_controls=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["status"] == "OBSERVED" and result["input_tokens"] == 20
    assert len(seen) == 1
    assert "private-test-key" not in json.dumps(result)
    assert "private-test-answer" not in json.dumps(result)
    assert result["scope"] == "single_model_call_only"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]}),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 513, "completion_tokens": 10},
            },
        ),
        httpx.Response(302, headers={"Location": "https://attacker.example.com/"}),
        httpx.Response(500, text="secret in provider response"),
    ],
)
def test_unknown_usage_fails_closed_and_never_retries(response):
    seen = []

    def handler(request):
        seen.append(request)
        return response

    result = smoke_once(
        dataset(),
        config(),
        credential="secret",
        execute=True,
        confirmed_external_controls=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
    assert len(seen) == 1


def test_preflight_failures_happen_before_network():
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request))
    with pytest.raises(ValueError):
        smoke_once(
            dataset("sensitive injected input"),
            config(),
            credential="key",
            execute=True,
            confirmed_external_controls=True,
            transport=transport,
        )
    with pytest.raises(ValueError):
        smoke_once(dataset(), config(), credential="key", execute=False, transport=transport)
    with pytest.raises(ValueError):
        smoke_once(
            dataset(),
            config(),
            credential="",
            execute=True,
            confirmed_external_controls=True,
            transport=transport,
        )
    with pytest.raises(ValueError):
        smoke_once(
            dataset(),
            config(max_cost="0.000001"),
            credential="key",
            execute=True,
            confirmed_external_controls=True,
            transport=transport,
        )
    with pytest.raises(ValueError):
        smoke_once(
            dataset(),
            config(endpoint="https://untrusted.example/v1", allowed_hosts=["untrusted.example"]),
            credential="key",
            execute=True,
            confirmed_external_controls=True,
            transport=transport,
        )
    assert not seen


def test_cli_executes_only_with_explicit_ack_and_mock_transport(tmp_path, capsys, monkeypatch):
    from pathlib import Path

    from app.evaluation.task_eval import model_smoke
    from app.evaluation.task_eval.budget_store import DurableBudget
    from app.evaluation.task_eval.cli import main

    source = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    path = tmp_path / "config.json"
    path.write_text(config(currency="CNY").model_dump_json(), encoding="utf-8")
    budget_path = tmp_path / "budget.sqlite"
    budget = DurableBudget(budget_path, limit_nano=1_000_000_000, opening_nano=10)
    monkeypatch.setenv("FITNESS_EVAL_MODEL_API_KEY", "never-output-this-key")
    original = model_smoke.smoke_once
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 20,
                },
            },
        )
    )
    monkeypatch.setattr(
        model_smoke, "smoke_once", lambda *a, **kw: original(*a, **kw, transport=transport)
    )
    args = [
        "model-smoke",
        "--dataset",
        str(source),
        "--config",
        str(path),
        "--execute",
        "--budget-file",
        str(budget_path),
        "--budget-limit-nano",
        "1000000000",
        "--budget-opening-nano",
        "10",
    ]
    with pytest.raises(SystemExit):
        main(args)
    capsys.readouterr()
    assert main([*args, "--ack-provider-cap-and-isolation"]) == 3
    assert budget.snapshot()["reserved_nano"] > 10
    output = capsys.readouterr()
    assert "never-output-this-key" not in output.out + output.err
    assert json.loads(output.out)["status"] == "OBSERVED"


def response_payload(*, finish="stop", usage_updates=None, message_updates=None):
    usage = {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 0},
    }
    usage.update(usage_updates or {})
    message = {"content": "private-response"}
    message.update(message_updates or {})
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}


def test_connectivity_is_single_call_with_bounded_cny_reservation_and_honest_provenance():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.deepseek.com"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 256
        assert "tools" not in body
        return httpx.Response(200, json=response_payload())

    result = connectivity_once(
        dataset(),
        credential="private-key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
    )
    assert len(requests) == 1
    assert result["status"] == "OBSERVED"
    assert result["reserved_cost"] == "0.030720000000"
    assert result["currency"] == "CNY"
    assert result["actual_cost"] is None
    assert result["provider_spend_cap"] == "NOT_VERIFIED"
    assert result["environment_isolation"] == "NOT_VERIFIED"
    assert "private-key" not in json.dumps(result)
    assert "private-response" not in json.dumps(result)


@pytest.mark.parametrize(
    "usage_updates",
    [
        {"total_tokens": 31},
        {"prompt_cache_hit_tokens": 1},
        {"prompt_cache_hit_tokens": True},
        {"prompt_cache_miss_tokens": 19},
        {"prompt_tokens_details": {"cached_tokens": 5}},
        {"prompt_tokens_details": {"cached_tokens": False}},
        {"server_tool_use": {"tool_search": 1}},
        {"completion_tokens_details": {"reasoning_tokens": 11}},
    ],
)
def test_inconsistent_or_unpriced_usage_is_unknown(usage_updates):
    result = connectivity_once(
        dataset(),
        credential="key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_payload(usage_updates=usage_updates))
        ),
    )
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
    assert result["estimated_cost"] is None


@pytest.mark.parametrize(
    "finish,expected",
    [
        ("length", "MODEL_OUTPUT_TRUNCATED"),
        ("content_filter", "MODEL_DID_NOT_FINISH_NORMALLY"),
        ("untrusted-provider-text", "MODEL_DID_NOT_FINISH_NORMALLY"),
    ],
)
def test_nonterminal_or_truncated_responses_never_become_observed(finish, expected):
    result = connectivity_once(
        dataset(),
        credential="key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_payload(finish=finish))
        ),
    )
    assert result["status"] == "INCOMPLETE" and result["reason"] == expected
    assert "untrusted-provider-text" not in json.dumps(result)


def test_unexpected_reasoning_and_tools_are_not_recorded_or_accepted():
    payload = response_payload(message_updates={"reasoning_content": "secret-thought"})
    result = connectivity_once(
        dataset(),
        credential="key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    assert result["reason"] == "UNEXPECTED_REASONING_OR_TOOL_CALL"
    assert "secret-thought" not in json.dumps(result)


def test_connectivity_does_not_run_without_explicit_confirmations():
    for single, prices in ((False, True), (True, False)):
        with pytest.raises(ValueError):
            connectivity_once(
                dataset(),
                credential="key",
                confirmed_single_call=single,
                confirmed_current_pricing=prices,
            )


def test_connectivity_and_sandbox_smoke_have_distinct_output_limits():
    payload = response_payload(usage_updates={"completion_tokens": 128, "total_tokens": 148})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    connectivity = connectivity_once(
        dataset(),
        credential="key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=transport,
    )
    assert connectivity["status"] == "OBSERVED"
    original = smoke_once(
        dataset(),
        config(),
        credential="key",
        execute=True,
        confirmed_external_controls=True,
        transport=transport,
    )
    assert original["status"] == "INCOMPLETE"
    assert original["reason"] == "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"


def test_connectivity_rejects_usage_above_256():
    payload = response_payload(usage_updates={"completion_tokens": 257, "total_tokens": 277})
    result = connectivity_once(
        dataset(),
        credential="key",
        confirmed_single_call=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
    assert result["estimated_cost"] is None
