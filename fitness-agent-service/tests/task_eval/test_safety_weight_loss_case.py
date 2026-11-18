from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import safety_weight_loss_case as probe
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_observation_does_not_claim_medical_or_task_safety():
    report = await probe.evaluate_weight_loss_observation(load_dataset(DATASET), split="dev")
    row = report["cases"][0]
    assert row["component_status"] == "OBSERVED"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["model_calls"] == 1
    assert row["observed"]["output_matches_neutral_stub"]
    assert probe._MARKER not in str(report)


@pytest.mark.asyncio
async def test_drift_prevents_execution():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "SAFE-014")
    changed = case.model_copy(
        update={"actor": case.actor.model_copy(update={"user_ref": "different"})}
    )
    report = await probe.evaluate_weight_loss_observation(
        replace(dataset, cases=[changed]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"
