from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import component_outreach_cases as probes
from app.evaluation.task_eval.dataset import load_dataset
from app.proactive.events import ProactiveNotificationTarget

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_outreach_probes_are_observations_not_task_results():
    dataset = load_dataset(DATASET)
    rows = (await probes.evaluate_outreach_case_observations(dataset, split="test"))["cases"]
    assert {row["case_id"] for row in rows} == {"OPER-014", "OPER-015", "OPER-016"}
    assert all(
        row["component_status"] == "PASS" and row["task_status"] == "UNKNOWN" for row in rows
    )


@pytest.mark.asyncio
async def test_outreach_contract_change_fails_closed():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-014")
    changed = case.model_copy(
        update={"expected": case.expected.model_copy(update={"reference_points": ["other"]})}
    )
    result = await probes.evaluate_outreach_case_observations(
        replace(dataset, cases=[changed]), split="test"
    )
    assert result["cases"][0]["component_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_outreach_probes_detect_target_duplicate_and_missing_subject_regression(monkeypatch):
    dataset = load_dataset(DATASET)
    cases = [c for c in dataset.cases if c.case_id in {"OPER-014", "OPER-016"}]
    monkeypatch.setattr(
        probes,
        "notification_targets",
        lambda event: (
            ProactiveNotificationTarget("same", "STUDENT"),
            ProactiveNotificationTarget("same", "COACH"),
        ),
    )
    monkeypatch.setattr(probes, "_candidate_specs", lambda event: [{}])
    result = await probes.evaluate_outreach_case_observations(
        replace(dataset, cases=cases), split="test"
    )
    assert all(row["component_status"] == "FAIL" for row in result["cases"])


@pytest.mark.asyncio
async def test_outreach_probe_reports_worker_failure(monkeypatch):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-015")

    async def failing_worker(*, case_id):
        assert case_id == "OPER-009"
        return {
            "cases": [
                {
                    "case_id": "OPER-009",
                    "component_status": "FAIL",
                    "observed": {"delivery_count": 1},
                }
            ]
        }

    monkeypatch.setattr(probes, "evaluate_notification_worker", failing_worker)
    result = await probes.evaluate_outreach_case_observations(
        replace(dataset, cases=[case]), split="test"
    )
    assert result["cases"][0]["component_status"] == "FAIL"
    assert result["cases"][0]["task_status"] == "UNKNOWN"
