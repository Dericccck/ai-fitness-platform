from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.task_eval import routing_model_probe
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.routing_model_probe import DEV_IDS, evaluate_dev_routing_model

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
KEY = "synthetic-evaluation-key"


def reply(usage: Any = "valid") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "intents": [
                                {"route": "FITNESS_COACHING", "intent": "FITNESS_KNOWLEDGE_QA"}
                            ],
                            "confidence": 0.99,
                            "reason_code": "TEST_ROUTING",
                        }
                    )
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        }
        if usage == "valid"
        else usage,
    }


def ledger(tmp_path: Path) -> DurableBudget:
    return DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)


async def run(
    dataset: Any, budget: DurableBudget, handler: Any, max_calls: int = 8
) -> dict[str, Any]:
    return await evaluate_dev_routing_model(
        dataset,
        credential=KEY,
        budget=budget,
        max_calls=max_calls,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
    )


async def test_bounded_sequential_real_router_and_no_raw_output(tmp_path: Path) -> None:
    budget = ledger(tmp_path)
    calls = []
    marker = "private-model-content"

    def handler(request: httpx.Request) -> httpx.Response:
        assert budget.snapshot()["pending"] == 1
        assert request.url == "https://api.deepseek.com/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"} and "tools" not in body
        calls.append(request)
        payload = reply()
        payload["other"] = marker
        return httpx.Response(200, json=payload)

    report = await run(load_dataset(DATASET), budget, handler, max_calls=1)
    assert len(calls) == report["model_calls"] == 1
    assert all(row["task_status"] == "UNKNOWN" for row in report["cases"])
    assert {row["case_id"] for row in report["cases"]} == set(DEV_IDS)
    assert marker not in json.dumps(report) and KEY not in json.dumps(report)
    assert budget.snapshot()["reserved_nano"] == 100_000_000
    assert not budget.snapshot()["frozen"] and not budget.snapshot()["pending"]


@pytest.mark.parametrize(
    "kind", ["unknown_usage", "redirect", "timeout", "server_tool", "truncated"]
)
async def test_uncertain_or_anomalous_request_freezes_and_stops_batch(
    tmp_path: Path, kind: str
) -> None:
    budget = ledger(tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example/"})
        if kind == "timeout":
            raise httpx.ReadTimeout("sensitive", request=request)
        data = reply(usage=None if kind == "unknown_usage" else "valid")
        if kind == "server_tool":
            data["choices"][0]["message"]["server_tool_calls"] = [{"secret": "private"}]
        if kind == "truncated":
            data["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=data)

    report = await run(load_dataset(DATASET), budget, handler)
    assert report["model_calls"] == len(calls) == 1
    assert budget.snapshot()["frozen"] and not budget.snapshot()["pending"]
    assert any(row.get("reason") == "BUDGET_FROZEN_OR_PENDING" for row in report["cases"])
    assert "private" not in json.dumps(report) and KEY not in json.dumps(report)


async def test_changed_case_prevents_any_network(tmp_path: Path) -> None:
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == DEV_IDS[0])
    changed = case.model_copy(
        update={"steps": [case.steps[0].model_copy(update={"message": "modified"}), case.steps[1]]}
    )
    dataset = replace(
        dataset, cases=[changed if c.case_id == changed.case_id else c for c in dataset.cases]
    )
    calls = []
    with pytest.raises(ValueError):
        await run(dataset, ledger(tmp_path), lambda request: calls.append(request))
    assert not calls


async def test_schema_diagnostics_do_not_disclose_untrusted_fields(tmp_path: Path) -> None:
    budget = ledger(tmp_path)

    def handler(_):
        payload = reply()
        payload["choices"][0]["message"]["content"] = json.dumps(
            {
                "intents": "invalid",
                "confidence": 0.4,
                "reason_code": "LOW",
                "secret_extra": "secret-value",
            }
        )
        return httpx.Response(200, json=payload)

    report = await run(load_dataset(DATASET), budget, handler, max_calls=1)
    row = next(
        row for row in report["cases"] if row.get("model_status") == "MODEL_RESPONSE_RECEIVED"
    )
    assert {entry["error"] for entry in row["schema_errors"]} == {"list_type", "extra_forbidden"}
    assert "secret_extra" not in json.dumps(report) and "secret-value" not in json.dumps(report)
    assert row["task_status"] == "UNKNOWN"


def test_cli_uses_existing_budget_and_never_overwrites_output(tmp_path, monkeypatch, capsys):
    key = tmp_path / "key"
    key.write_text(KEY)
    key.chmod(0o600)
    budget_path = tmp_path / "budget.sqlite"
    DurableBudget(budget_path, limit_nano=1_000_000_000, opening_nano=0)
    output = tmp_path / "report.json"
    calls = []

    async def fake_evaluate(dataset, **kwargs):
        calls.append(kwargs["credential"])
        return {"task_status": "UNKNOWN", "model_calls": 0}

    monkeypatch.setattr(routing_model_probe, "evaluate_dev_routing_model", fake_evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe",
            "--dataset",
            str(DATASET),
            "--credential-file",
            str(key),
            "--budget-file",
            str(budget_path),
            "--budget-limit-nano",
            "1000000000",
            "--budget-opening-nano",
            "0",
            "--minimum-reserved-nano",
            "0",
            "--max-calls",
            "1",
            "--execute",
            "--confirm-current-pricing",
            "--report-file",
            str(output),
        ],
    )
    assert routing_model_probe.main() == 3
    assert json.loads(output.read_text())["task_status"] == "UNKNOWN"
    assert calls == [KEY]
    assert KEY not in capsys.readouterr().out
    assert routing_model_probe.main() == 1
    assert len(calls) == 1
