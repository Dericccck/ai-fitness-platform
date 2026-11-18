"""Synthetic component protocols preserve unanswered task and media boundaries."""

from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.knowledge_extended_local import evaluate_knowledge_extended


@pytest.fixture
def dataset():
    return load_dataset(Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl")


@pytest.mark.asyncio
async def test_training_and_media_checks_remain_component_observations(dataset):
    result = await evaluate_knowledge_extended(dataset)
    cases = {row["case_id"]: row for row in result["cases"]}
    assert len(cases) == 12
    assert all(row["status"] == "OBSERVED" for row in cases.values())
    assert all(row["answer"]["status"] == "UNKNOWN" for row in cases.values())
    assert {"goal_type", "training_days", "level", "session_minutes"} <= set(
        cases["KNOW-019"]["actual"]["missing_structured_fields"]
    )
    assert cases["KNOW-020"]["actual"]["diagnosis_phrase_rejected"]
    assert cases["KNOW-021"]["actual"]["memory_in_prompt"]
    assert cases["KNOW-021"]["actual"]["memory_ref_semantics"] == "MODEL_DECLARED_NOT_VERIFIED_USE"
    assert cases["KNOW-022"]["actual"]["days"] == 2
    assert cases["KNOW-023"]["actual"]["equipment"] == []
    assert cases["KNOW-024"]["actual"]["memory_in_prompt"]
    assert cases["KNOW-024"]["actual"]["days"] == 1
    for case_id in ("KNOW-027", "KNOW-028"):
        assert cases[case_id]["actual"] == {"missing_media_rejected": True, "provider_calls": 0}
    for case_id in ("KNOW-025", "KNOW-026", "KNOW-029", "KNOW-030"):
        assert cases[case_id]["actual"]["assessment"] == "NEEDS_CLARIFICATION"
        assert cases[case_id]["answer"]["reason"] == "NO_ACTUAL_MEDIA_OR_VISION_MODEL"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_days", "equipment", "forged_memory"])
async def test_invalid_fixed_model_output_is_rejected_by_real_service(dataset, fault):
    result = await evaluate_knowledge_extended(dataset, fault=fault)
    rows = [row for row in result["cases"] if "KNOW-021" <= row["case_id"] <= "KNOW-024"]
    assert len(rows) == 4
    assert all(row["status"] == "REJECTED" for row in rows)


@pytest.mark.asyncio
async def test_drifted_question_is_not_mislabeled(dataset):
    case = next(c for c in dataset.cases if c.case_id == "KNOW-022")
    case.steps[0].message = "每周练五次"
    rows = (await evaluate_knowledge_extended(dataset))["cases"]
    row = next(row for row in rows if row["case_id"] == "KNOW-022")
    assert row["status"] == "SKIPPED"
    assert row["reason"] == "FIXTURE_CASE_MISMATCH"
