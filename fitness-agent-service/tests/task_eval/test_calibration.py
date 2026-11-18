from copy import deepcopy
from pathlib import Path

import pytest

from app.evaluation.task_eval.calibration import summarize_calibration
from app.evaluation.task_eval.dataset import load_dataset


@pytest.fixture
def dataset():
    return load_dataset(Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl")


def rating(case_id, human, judge, metric="task_result"):
    return {
        "case_id": case_id,
        "metric_id": metric,
        "human_score": human,
        "judge_score": judge,
        "human_evidence_sha256": "a" * 64 if human is not None else None,
        "judge_evidence_sha256": "b" * 64 if judge is not None else None,
    }


def bundle(dataset, rows):
    return {
        "schema_version": "1.0",
        "dataset_sha256": dataset.sha256,
        "rubric_ref": "rubric:v1",
        "judge_model": "judge-v1",
        "prompt_version": "prompt-v1",
        "ratings": rows,
    }


def test_counts_include_unknown_and_expose_severe_risk_misses(dataset):
    cases = [c for c in dataset.cases if c.split == "dev"]
    rows = [
        rating(cases[0].case_id, 0, 2),
        rating(cases[1].case_id, 2, 2),
        rating(cases[2].case_id, None, 1),
        rating(cases[3].case_id, 0, None),
    ]
    result = summarize_calibration(dataset, bundle(dataset, rows))
    overall = result["overall"]
    assert overall["samples"] == 4
    assert overall["exact_agreement"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert overall["either_unknown"] == 2
    assert overall["confusion_matrix_human_by_judge"]["0"]["2"] == 1
    assert overall["human_failure_judge_score_2"] == 1
    assert sum(group["samples"] for group in result["by_risk"].values()) == 4


def test_zero_comparable_is_null(dataset):
    cases = [c for c in dataset.cases if c.split == "dev"]
    rows = [rating(cases[0].case_id, None, 1), rating(cases[1].case_id, 0, None)]
    result = summarize_calibration(dataset, bundle(dataset, rows))
    assert result["overall"]["exact_agreement"]["value"] is None
    assert result["by_metric"]["task_result:semantic"]["comparable"] == 0


def test_safety_score_one_is_unknown_and_is_not_mixed_with_semantic_score_one(dataset):
    safety = next(c for c in dataset.cases if c.case_id == "SAFE-013")
    semantic = next(c for c in dataset.cases if c.case_id == "ROUT-001")
    rows = [rating(safety.case_id, 0, 1), rating(semantic.case_id, 1, 1)]
    report = summarize_calibration(dataset, bundle(dataset, rows))
    safety_stats = report["by_metric"]["task_result:no_severe_unsafe_guidance"]
    assert safety_stats["comparable"] == 0
    assert safety_stats["judge_missing"] == 0
    assert safety_stats["judge_unknown"] == 1
    assert safety_stats["human_failure_judge_unknown"] == 1
    assert report["by_metric"]["task_result:semantic"]["comparable"] == 1


def test_invalid_or_unbound_observations_fail_closed(dataset):
    dev = next(c for c in dataset.cases if c.split == "dev")
    test = next(c for c in dataset.cases if c.split == "test")
    good = rating(dev.case_id, 0, 2)
    for bad in (
        {**good, "human_score": True},
        {**good, "human_score": 0.5},
        {**good, "human_evidence_sha256": None},
        {**good, "judge_score": None},
        {**good, "metric_id": "not-an-assertion"},
        {**good, "case_id": test.case_id},
        {**good, "answer": "private content"},
    ):
        with pytest.raises(ValueError):
            summarize_calibration(dataset, bundle(dataset, [bad]))
    with pytest.raises(ValueError, match="duplicate"):
        summarize_calibration(dataset, bundle(dataset, [good, deepcopy(good)]))
    bad_bundle = bundle(dataset, [good])
    bad_bundle["dataset_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        summarize_calibration(dataset, bad_bundle)
