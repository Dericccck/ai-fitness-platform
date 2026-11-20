from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.task_eval import runner_dev_routing as module
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
KEY = "synthetic-evaluation-key"


def _reply(confidence: float = 0.2) -> dict[str, Any]:
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
                            "confidence": confidence,
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
        },
    }


@pytest.mark.asyncio
async def test_real_supervisor_model_prompt_budget_and_clarification(tmp_path: Path) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert budget.snapshot()["pending"] == 1
        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        assert user == {
            "previous_route": None,
            "recent_messages": [{"role": "user", "content": "周六下午"}],
            "current_message": "周六下午",
        }
        assert "tools" not in body
        requests.append(request)
        return httpx.Response(200, json=_reply())

    observed = await module._observe(KEY, budget, transport=httpx.MockTransport(respond))
    assert len(requests) == observed["model_attempts"] == 1
    assert observed["model_status"] == "MODEL_RESPONSE_RECEIVED"
    assert observed["component_status"] == "OBSERVED"
    assert observed["supervisor_completed"] and observed["clarification"]
    assert observed["tool_steps"] == observed["disabled_domain_calls"] == 0
    assert observed["model_response_decision"] == {
        "stage": "model_response_before_router_policy",
        "confidence": 0.2,
        "intents": [
            {
                "route": "FITNESS_COACHING",
                "intent": "FITNESS_KNOWLEDGE_QA",
                "is_write": False,
            }
        ],
    }
    assert observed["supervisor_final"]["clarification"] is True
    assert observed["supervisor_final"]["route"] == "CLARIFICATION"
    assert KEY not in json.dumps(observed)
    assert budget.snapshot()["reserved_nano"] == 100_000_000


@pytest.mark.asyncio
async def test_domain_model_remains_disabled_when_router_selects_domain(tmp_path: Path) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    observed = await module._observe(
        KEY, budget, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_reply(0.99)))
    )
    assert observed["model_attempts"] == 1
    assert observed["model_status"] == "MODEL_RESPONSE_RECEIVED"
    assert observed["disabled_domain_calls"] == 1
    assert not observed["supervisor_completed"]
    assert observed["supervisor_final"] is None
    assert observed["model_response_decision"]["confidence"] == 0.99
    assert observed["model_response_decision"]["intents"][0]["intent"] == "FITNESS_KNOWLEDGE_QA"


@pytest.mark.asyncio
async def test_provider_failure_is_not_model_observation_and_freezes_budget(tmp_path: Path) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    observed = await module._observe(
        KEY, budget, transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    assert observed["model_attempts"] == 1
    assert observed["component_status"] == "INCONCLUSIVE"
    assert observed["model_response_decision"] is None
    assert budget.snapshot()["frozen"]


def _options(tmp_path: Path) -> dict[str, Any]:
    key = tmp_path / "key"
    key.write_text(KEY)
    key.chmod(0o600)
    budget = tmp_path / "budget.sqlite"
    DurableBudget(budget, limit_nano=10_000_000_000)
    return {
        "credential_file": str(key),
        "budget_file": str(budget),
        "budget_limit_nano": 10_000_000_000,
        "budget_opening_nano": 0,
        "minimum_reserved_nano": 0,
        "confirm_current_pricing": True,
    }


def test_standard_report_stays_incomplete_with_model_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def observe(*args: Any) -> dict[str, Any]:
        return {"model_attempts": 1, "component_status": "OBSERVED", "supervisor_completed": True}

    monkeypatch.setattr(module, "_observe", observe)
    options = _options(tmp_path)
    output = tmp_path / "report"
    manifest, trials, summary = module.execute_development_routing(
        load_dataset(DATASET),
        split="dev",
        mode="model_sandbox",
        repeats=1,
        seed=0,
        output_dir=str(output),
        case_id="ROUT-012",
        real_model_options=options,
    )
    assert manifest["status"] == "INCOMPLETE" and manifest["review"] == "draft"
    assert manifest["model_call_attempted"] is True
    assert trials[0]["status"] == "INCONCLUSIVE"
    assert all(a["status"] == "UNKNOWN" for a in trials[0]["assertions"])
    assert summary["trial_counts"]["INCONCLUSIVE"] == 1
    assert {p.name for p in output.iterdir()} == {
        "manifest.json",
        "trials.jsonl",
        "summary.json",
        "report.md",
    }
    assert KEY not in (output / "trials.jsonl").read_text()


def test_cli_dispatches_fixed_routing_case_without_promoting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.evaluation.task_eval.cli import main

    async def observe(*args: Any) -> dict[str, Any]:
        return {"model_attempts": 1, "component_status": "OBSERVED", "supervisor_completed": True}

    monkeypatch.setattr(module, "_observe", observe)
    options = _options(tmp_path)
    output = tmp_path / "report"
    code = main(
        [
            "run",
            "--dataset",
            str(DATASET),
            "--split",
            "dev",
            "--mode",
            "model_sandbox",
            "--execute",
            "--dev-real-model-case",
            "ROUT-012",
            "--credential-file",
            options["credential_file"],
            "--budget-file",
            options["budget_file"],
            "--budget-limit-nano",
            str(options["budget_limit_nano"]),
            "--budget-opening-nano",
            "0",
            "--minimum-reserved-nano",
            "0",
            "--confirm-current-pricing",
            "--output-dir",
            str(output),
        ]
    )
    assert code == 3
    assert json.loads((output / "manifest.json").read_text())["status"] == "INCOMPLETE"
    assert json.loads((output / "trials.jsonl").read_text())["case_id"] == "ROUT-012"


def test_contract_drift_fails_before_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args: Any) -> Any:
        raise AssertionError("must not call model")

    monkeypatch.setattr(module, "_observe", forbidden)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-012")
    changed = case.model_copy(update={"split": "test"})
    dataset = replace(
        dataset, cases=[changed if c.case_id == case.case_id else c for c in dataset.cases]
    )
    with pytest.raises(ValueError, match="contract changed"):
        module.execute_development_routing(
            dataset,
            split="dev",
            mode="model_sandbox",
            repeats=1,
            seed=0,
            output_dir=str(tmp_path / "report"),
            case_id="ROUT-012",
            real_model_options=_options(tmp_path),
        )
    assert not (tmp_path / "report").exists()


def test_existing_report_directory_stops_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args: Any) -> Any:
        raise AssertionError("must not call model")

    monkeypatch.setattr(module, "_observe", forbidden)
    options = _options(tmp_path)
    output = tmp_path / "report"
    output.mkdir()
    (output / "existing").write_text("unchanged")
    with pytest.raises(FileExistsError):
        module.execute_development_routing(
            load_dataset(DATASET),
            split="dev",
            mode="model_sandbox",
            repeats=1,
            seed=0,
            output_dir=str(output),
            case_id="ROUT-012",
            real_model_options=options,
        )
    assert (output / "existing").read_text() == "unchanged"


def test_frozen_budget_stops_before_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*args: Any) -> Any:
        raise AssertionError("must not call model")

    monkeypatch.setattr(module, "_observe", forbidden)
    options = _options(tmp_path)
    budget = DurableBudget.open_existing(
        options["budget_file"], limit_nano=10_000_000_000, opening_nano=0
    )
    budget.reserve("pending-call", 100_000_000)
    budget.finish("pending-call", usage_known=False, reason_code="PROVIDER_HTTP_ERROR")
    with pytest.raises(ValueError, match="budget history unavailable"):
        module.execute_development_routing(
            load_dataset(DATASET),
            split="dev",
            mode="model_sandbox",
            repeats=1,
            seed=0,
            output_dir=str(tmp_path / "report"),
            case_id="ROUT-012",
            real_model_options=options,
        )
    assert not (tmp_path / "report").exists()


def test_non_private_credential_stops_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args: Any) -> Any:
        raise AssertionError("must not call model")

    monkeypatch.setattr(module, "_observe", forbidden)
    options = _options(tmp_path)
    Path(options["credential_file"]).chmod(0o644)
    with pytest.raises(ValueError, match="private credential unavailable"):
        module.execute_development_routing(
            load_dataset(DATASET),
            split="dev",
            mode="model_sandbox",
            repeats=1,
            seed=0,
            output_dir=str(tmp_path / "report"),
            case_id="ROUT-012",
            real_model_options=options,
        )
    assert not (tmp_path / "report").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,message", list(module.SUPPORTED_ROUTING_CASES.items()))
async def test_each_frozen_case_uses_supervisor_and_one_exact_synthetic_request(
    tmp_path: Path, case_id: str, message: str
) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert budget.snapshot()["pending"] == 1
        body = json.loads(request.content)
        assert json.loads(body["messages"][1]["content"]) == {
            "previous_route": None,
            "recent_messages": [{"role": "user", "content": message}],
            "current_message": message,
        }
        assert "tools" not in body
        return httpx.Response(200, json=_reply())

    observed = await module._observe(KEY, budget, case_id, transport=httpx.MockTransport(respond))
    assert len(requests) == observed["model_attempts"] == 1
    assert observed["model_status"] == "MODEL_RESPONSE_RECEIVED"
    assert observed["disabled_domain_calls"] == 0
    assert observed["supervisor_completed"] and observed["clarification"]
    assert observed["tool_steps"] == 0
    assert budget.snapshot()["reserved_nano"] == 100_000_000


@pytest.mark.asyncio
async def test_unknown_case_is_rejected_without_budget_or_request(tmp_path: Path) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    calls = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("must not call")

    with pytest.raises(ValueError, match="unsupported routing case"):
        await module._observe(KEY, budget, "ROUT-013", transport=httpx.MockTransport(forbidden))
    assert calls == [] and budget.snapshot()["reserved_nano"] == 0


@pytest.mark.parametrize("case_id", list(module.SUPPORTED_ROUTING_CASES))
def test_case_id_and_report_are_bound_to_exact_frozen_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_id: str
) -> None:
    async def observe(credential: str, budget: DurableBudget, selected: str) -> dict[str, Any]:
        assert selected == case_id
        return {"model_attempts": 1, "component_status": "OBSERVED", "supervisor_completed": True}

    monkeypatch.setattr(module, "_observe", observe)
    output = tmp_path / "report"
    manifest, trials, _ = module.execute_development_routing(
        load_dataset(DATASET),
        split="dev",
        mode="model_sandbox",
        repeats=1,
        seed=0,
        output_dir=str(output),
        case_id=case_id,
        real_model_options=_options(tmp_path),
    )
    assert manifest["selected_case_ids"] == [case_id]
    assert trials[0]["case_id"] == case_id and trials[0]["status"] == "INCONCLUSIVE"
    assert all(row["status"] == "UNKNOWN" for row in trials[0]["assertions"])


def test_credential_final_symlink_is_rejected(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text(KEY)
    key.chmod(0o600)
    linked = tmp_path / "link"
    linked.symlink_to(key)
    with pytest.raises(ValueError, match="private credential unavailable"):
        module._open_private_credential(str(linked))


def test_credential_requires_current_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "key"
    key.write_text(KEY)
    key.chmod(0o600)
    monkeypatch.setattr(module.os, "geteuid", lambda: key.stat().st_uid + 1)
    with pytest.raises(ValueError, match="private credential unavailable"):
        module._open_private_credential(str(key))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_invalid_model_output_cannot_be_observed(
    tmp_path: Path, missing: bool
) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    payload = _reply()
    payload["choices"][0]["message"]["content"] = "" if missing else "{}"
    observed = await module._observe(
        KEY,
        budget,
        "ROUT-011",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    assert observed["model_attempts"] == 1
    assert observed["component_status"] == "INCONCLUSIVE"
    assert observed["model_response_decision"] is None
    if missing:
        assert observed["model_status"] == "MODEL_OUTPUT_MISSING"
        assert budget.snapshot()["frozen"]
    else:
        assert observed["schema_errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intents,confidence",
    [
        ([{"route": "UNKNOWN_ROUTE", "intent": "FITNESS_KNOWLEDGE_QA"}], 0.9),
        ([{"route": "BOOKING", "intent": "FITNESS_KNOWLEDGE_QA"}], 0.9),
        (
            [
                {"route": "FITNESS_COACHING", "intent": "FITNESS_KNOWLEDGE_QA"},
                {"route": "BOOKING", "intent": "SECRET_PROVIDER_TEXT"},
            ],
            0.9,
        ),
        ([{"route": "FITNESS_COACHING", "intent": "FITNESS_KNOWLEDGE_QA"}], 1.1),
    ],
)
async def test_no_partial_or_unvalidated_model_decision(
    tmp_path: Path, intents: list[dict[str, str]], confidence: float
) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    payload = _reply()
    payload["choices"][0]["message"]["content"] = json.dumps(
        {"intents": intents, "confidence": confidence, "reason_code": "SECRET_PROVIDER_TEXT"}
    )
    observed = await module._observe(
        KEY,
        budget,
        "ROUT-011",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    assert observed["model_response_decision"] is None
    assert observed["component_status"] == "INCONCLUSIVE"
    assert "SECRET_PROVIDER_TEXT" not in json.dumps(observed)


@pytest.mark.asyncio
async def test_model_reason_and_unrelated_payload_are_not_retained(tmp_path: Path) -> None:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    payload = _reply(0.99)
    payload["choices"][0]["message"]["content"] = json.dumps(
        {
            "intents": [
                {"route": "BOOKING", "intent": "QUERY_APPOINTMENTS"},
                {"route": "BOOKING", "intent": "CANCEL_BOOKING"},
            ],
            "confidence": 0.99,
            "reason_code": "SECRET_PROVIDER_TEXT",
        }
    )
    payload["unused"] = "OTHER_PROVIDER_TEXT"
    observed = await module._observe(
        KEY,
        budget,
        "ROUT-017",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    assert observed["model_response_decision"] == {
        "stage": "model_response_before_router_policy",
        "confidence": 0.99,
        "intents": [
            {"route": "BOOKING", "intent": "QUERY_APPOINTMENTS", "is_write": False},
            {"route": "BOOKING", "intent": "CANCEL_BOOKING", "is_write": True},
        ],
    }
    assert "SECRET_PROVIDER_TEXT" not in json.dumps(observed)
    assert "OTHER_PROVIDER_TEXT" not in json.dumps(observed)
