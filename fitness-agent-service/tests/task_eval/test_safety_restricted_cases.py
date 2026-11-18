from dataclasses import replace
from pathlib import Path

from app.evaluation.task_eval import safety_restricted_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def test_restricted_template_probe_is_not_full_task_success():
    report = probes.evaluate_safety_restricted_observations(load_dataset(DATASET), split="test")
    row = report["cases"][0]
    assert row["component_status"] == "PASS"
    assert row["task_status"] == "UNKNOWN"


def test_case_drift_is_unknown():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "SAFE-015")
    changed = case.model_copy(
        update={"expected": case.expected.model_copy(update={"reference_points": ["changed"]})}
    )
    report = probes.evaluate_safety_restricted_observations(
        replace(dataset, cases=[changed]), split="test"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"


def test_template_or_route_bypass_fails(monkeypatch):
    dataset = load_dataset(DATASET)
    monkeypatch.setattr(probes, "_customer_service_restricted_answer", lambda message: "去吃某种药")
    report = probes.evaluate_safety_restricted_observations(dataset, split="test")
    assert report["cases"][0]["component_status"] == "FAIL"
    monkeypatch.setattr(
        probes, "_customer_service_restricted_answer", lambda message: probes._RESTRICTED_RESPONSE
    )
    monkeypatch.setattr(probes, "classify_route", lambda message: "FITNESS_COACHING")
    report = probes.evaluate_safety_restricted_observations(dataset, split="test")
    assert report["cases"][0]["component_status"] == "FAIL"
