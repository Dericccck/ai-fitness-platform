import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.evaluation.prompt_regression import (
    contains_unnegated_claim,
    digest,
    rescore_report,
    runtime_snapshot,
    score_output,
    validate_dataset,
)


def test_repository_dataset_has_distinct_business_and_adversarial_cases() -> None:
    data = json.loads((Path(__file__).parents[1] / "evals/prompt_regression.json").read_text())
    cases = validate_dataset(data)
    assert len(cases) >= 16
    assert any("injection" in case["id"] for case in cases)


async def test_snapshot_is_from_actual_runtime_and_has_no_secrets() -> None:
    settings = Settings(_env_file=None, llm_api_key="do-not-export-secret")
    snapshot = await runtime_snapshot(settings)
    assert "router" in snapshot["prompts"]
    assert "FITNESS_COACHING.FITNESS_KNOWLEDGE_QA" in snapshot["prompts"]
    assert "do-not-export-secret" not in json.dumps(snapshot)
    assert digest(snapshot) == digest(await runtime_snapshot(settings))


def test_regression_scoring_rejects_wrong_route_and_unauthorized_tools() -> None:
    case = {"prompt_id": "router", "expected": {"intents": [["BOOKING", "CANCEL_BOOKING"]]}}
    assert score_output(
        case, '{"intents":[{"route":"OPERATIONS","intent":"CANCEL_BOOKING"}]}', []
    ) == ["ROUTE_MISMATCH"]
    assert score_output(
        {"prompt_id": "BOOKING.QUERY_APPOINTMENTS", "expected": {"no_tools": True}}, "", ["write"]
    ) == ["UNEXPECTED_TOOL"]


def test_duplicate_case_ids_fail_contract() -> None:
    case = {"id": "a", "prompt_id": "router", "question": "问题"}
    with pytest.raises(ValueError):
        validate_dataset({"cases": [case, case]})


def test_negative_refusal_is_not_a_false_success_but_later_positive_claim_fails() -> None:
    assert not contains_unnegated_claim("我不能直接声称预约成功。必须确认。", "预约成功")
    assert contains_unnegated_claim("我不能直接声称预约成功，但已经预约成功了。", "预约成功")
    assert contains_unnegated_claim("不能否认预约成功。", "预约成功")
    assert contains_unnegated_claim("预约成功，已扣除课时。", "预约成功")


def test_rescore_preserves_real_outputs_and_original_schema_failures() -> None:
    dataset = {"cases": [{"id": "safe", "prompt_id": "router", "question": "预约"}]}
    row = {
        "case_id": "safe",
        "repeat": 0,
        "answer": '{"intents":[{"route":"BOOKING","intent":"CREATE_BOOKING"}]}',
        "tools": [],
        "failures": ["INVALID_TOOL_ARGUMENTS"],
    }
    original = {
        "mode": "live_model_comparison",
        "dataset_digest": digest(dataset),
        "repetitions": 1,
        "results": {"baseline": [row], "candidate": [row]},
        "passed": False,
    }
    report = rescore_report(original, dataset)
    assert not report["passed"] and report["rescored"]
    assert report["results"]["candidate"][0]["answer"] == row["answer"]
    assert "scorer_version" not in original
