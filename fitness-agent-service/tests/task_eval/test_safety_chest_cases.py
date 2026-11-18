from dataclasses import replace
from pathlib import Path

import pytest

from app.agent import supervisor
from app.evaluation.task_eval import safety_chest_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_real_supervisor_guard_does_not_call_adversarial_model():
    report = await probes.evaluate_chest_safety_observations(load_dataset(DATASET), split="dev")
    row = report["cases"][0]
    assert row["component_status"] == "PASS"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["model_calls"] == 0


@pytest.mark.asyncio
async def test_case_drift_is_unknown():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "SAFE-013")
    changed = case.model_copy(
        update={"expected": case.expected.model_copy(update={"reference_points": ["changed"]})}
    )
    report = await probes.evaluate_chest_safety_observations(
        replace(dataset, cases=[changed]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_removed_guard_is_caught(monkeypatch):
    monkeypatch.setattr(supervisor, "_urgent_training_safety_answer", lambda message: None)
    report = await probes.evaluate_chest_safety_observations(load_dataset(DATASET), split="dev")
    row = report["cases"][0]
    assert row["component_status"] == "FAIL"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["model_calls"] == 1
