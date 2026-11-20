import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.dev_semantic_judge import propose_judgment

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
ANSWER = "建议暂停会加重疼痛的动作，咨询专业人士，并了解不适的情况。"


def observation(dataset):
    return {
        "case_id": "KNOW-020",
        "dataset_sha256": dataset.sha256,
        "component_status": "OBSERVED",
        "answer_sha256": hashlib.sha256(ANSWER.encode()).hexdigest(),
    }


def provider_response(content):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 50,
            "total_tokens": 550,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 500,
        },
    }


@pytest.mark.asyncio
async def test_proposal_binds_model_input_and_evidence_but_never_passes_task(tmp_path):
    dataset = load_dataset(DATASET)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    requests = []

    def handle(req):
        requests.append(req)
        message = json.loads(json.loads(req.content)["messages"][1]["content"])
        assert message["answer"] == ANSWER
        assert message["reference_points"]
        return httpx.Response(
            200,
            json=provider_response(
                json.dumps(
                    {
                        "score": 2,
                        "reason": "答复支持参考点",
                        "evidence_ids": [message["answer_evidence_id"]],
                    },
                    ensure_ascii=False,
                )
            ),
        )

    result = await propose_judgment(
        dataset,
        "KNOW-020",
        "task_result",
        observation(dataset),
        ANSWER,
        credential="secret-key-123",
        budget=budget,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handle),
    )
    assert len(requests) == result["attempts"] == 1
    assert result["proposed_score"] == 2
    assert result["task_status"] == "UNKNOWN"
    assert result["human_review"] == "NOT_REVIEWED"
    assert result["calibration"] == "NOT_VERIFIED"
    assert len(result["rubric_sha256"]) == len(result["observation_sha256"]) == 64
    assert ANSWER not in json.dumps(result, ensure_ascii=False)
    assert budget.snapshot()["frozen"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        '{"score":2,"score":0,"reason":"x","evidence_ids":[]}',
        '{"score":true,"reason":"x","evidence_ids":[]}',
        '{"score":2,"reason":"x","evidence_ids":["invented"]}',
        '{"score":2,"reason":"x","evidence_ids":[],"extra":1}',
    ],
)
async def test_invalid_judgment_cannot_turn_into_a_pass(tmp_path, bad):
    dataset = load_dataset(DATASET)
    result = await propose_judgment(
        dataset,
        "KNOW-020",
        "task_result",
        observation(dataset),
        ANSWER,
        credential="secret-key-123",
        budget=DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000),
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=provider_response(bad))),
    )
    assert result["task_status"] == "UNKNOWN"
    assert result["reason"] == "INVALID_STRUCTURED_JUDGMENT"
    assert "proposed_score" not in result


@pytest.mark.asyncio
async def test_wrong_evidence_is_rejected_before_call(tmp_path):
    dataset = load_dataset(DATASET)
    calls = []
    with pytest.raises(ValueError, match="observation"):
        await propose_judgment(
            dataset,
            "KNOW-020",
            "task_result",
            observation(dataset),
            ANSWER + "different",
            credential="secret-key-123",
            budget=DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000),
            execute=True,
            confirmed_current_pricing=True,
            transport=httpx.MockTransport(lambda request: calls.append(request)),
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["other_case", "reference_points", "case_metadata", "declared_sha", "disk_sha"]
)
async def test_rejects_changed_frozen_contract_before_model_call(tmp_path, mutation, monkeypatch):
    from dataclasses import replace

    import app.evaluation.task_eval.dev_semantic_judge as judge

    dataset = load_dataset(DATASET)
    if mutation in ("reference_points", "case_metadata"):
        cases = [case.model_copy(deep=True) for case in dataset.cases]
        case = next(case for case in cases if case.case_id == "KNOW-020")
        if mutation == "reference_points":
            case.expected.assertions[0].parameters["reference_points"] = [
                "malicious changed rubric"
            ]
        else:
            cases[0].metadata.notes = "changed elsewhere"
        dataset = replace(dataset, cases=cases)
    elif mutation == "declared_sha":
        dataset = replace(dataset, sha256="0" * 64)
    elif mutation == "disk_sha":
        altered = tmp_path / "altered.jsonl"
        altered.write_bytes(DATASET.read_bytes() + b"\n")
        monkeypatch.setattr(judge, "_SOURCE_PATH", altered)
    calls = []
    case_id = "SAFE-013" if mutation == "other_case" else "KNOW-020"
    with pytest.raises(ValueError):
        await propose_judgment(
            dataset,
            case_id,
            "task_result",
            observation(dataset),
            ANSWER,
            credential="secret-key-123",
            budget=DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000),
            execute=True,
            confirmed_current_pricing=True,
            transport=httpx.MockTransport(lambda request: calls.append(request)),
        )
    assert calls == []
