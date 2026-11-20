from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import capacity_draft, dataset_local
from app.evaluation.task_eval.dataset import load_dataset


@pytest.fixture
def dataset():
    return load_dataset(Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl")


@pytest.mark.asyncio
async def test_capacity_observations_are_inconclusive_with_unknown_product_and_model(dataset):
    cases = [c for c in dataset.cases if c.case_id in {"BOOK-009", "BOOK-022"}]
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=cases))
    assert report["summary"]["task_status_counts"] == {"INCONCLUSIVE": 2}
    assert report["summary"]["component_failures"] == 0
    for row in report["cases"]:
        assert row["reason"] == "CAPACITY_DRAFT_DOUBLE_ONLY_NO_PRODUCT_CONTRACT"
        observation = row["observations"]["draft_capacity"]
        assert observation["component_status"] == "PASS"
        assert observation["model_status"] == observation["product_status"] == "UNKNOWN"
    assert "evaluation/task_eval/capacity_draft.py" in report["manifest"]["source_sha256"]
    assert (
        "evals/task_eval/dev_fixtures/book-capacity-draft-v1.json"
        in report["manifest"]["known_fixture_sha256"]
    )


@pytest.mark.asyncio
async def test_failed_component_is_counted_and_never_hidden(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-009")
    original = capacity_draft.observe

    def faulty(current):
        result = original(current)
        result["after"]["credits"] -= 1
        return result

    monkeypatch.setattr(capacity_draft, "observe", faulty)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    row = report["cases"][0]
    assert row["task_status"] == "FAIL"
    assert row["reason"] == "CAPACITY_DRAFT_COMPONENT_FAIL"
    assert report["summary"]["component_failures"] == 1
    observation = row["observations"]["draft_capacity"]
    assert observation["component_status"] == "FAIL"
    assert "last_seat_delta" in observation["violations"]
    assert observation["product_status"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [None, {}, {"violations": []}])
async def test_missing_component_observation_is_error(dataset, monkeypatch, broken):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-022")
    monkeypatch.setattr(capacity_draft, "observe", lambda _: broken)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    assert report["cases"][0]["task_status"] == "ERROR"
    assert report["cases"][0]["reason"] == "CAPACITY_DRAFT_COMPONENT_ERROR"
    assert "draft_capacity" not in report["cases"][0]["observations"]


@pytest.mark.asyncio
async def test_capacity_case_drift_is_error_without_private_exception(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-022").model_copy(deep=True)
    case.steps[0].message = "changed case"
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    assert report["cases"][0]["task_status"] == "ERROR"
    assert report["cases"][0]["reason"] == "CAPACITY_DRAFT_COMPONENT_ERROR"
