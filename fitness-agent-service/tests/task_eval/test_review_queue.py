from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.review_queue import build_review_queue


def test_queue_is_complete_and_does_not_create_review_decisions():
    dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    )
    queue = build_review_queue(dataset)
    assert queue["case_count"] == 120
    assert queue["dataset_sha256"] == dataset.sha256
    assert all(row["review_outcome"] is None for row in queue["cases"])
    assert all(row["case"]["review"]["status"] == "draft" for row in queue["cases"])
    assert all(len(row["case_sha256"]) == 64 for row in queue["cases"])
    book_018 = next(row for row in queue["cases"] if row["case_id"] == "BOOK-018")
    missing = {
        item["assertion_id"]: item["fields"] for item in book_018["missing_grader_parameters"]
    }
    assert missing["pending"] == ["actor", "action"]
    assert missing["rejected"] == ["actor", "action"]
    assert missing["task_result"] == ["metric_id", "prompt_version", "score_mapping"]
    assert queue["cases_with_missing_grader_parameters"] > 0
    assert book_018["review_outcome"] is None


def test_queue_rejects_non_synthetic_data():
    dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    )
    case = dataset.cases[0].model_copy(deep=True)
    case.source.type = "authorized_trial"
    with pytest.raises(ValueError, match="synthetic"):
        build_review_queue(replace(dataset, cases=[case]))


def test_case_fingerprint_changes_with_reviewed_content():
    dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    )
    case = dataset.cases[0].model_copy(deep=True)
    original = build_review_queue(replace(dataset, cases=[case]))["cases"][0]["case_sha256"]
    case.expected.reference_points.append("modified")
    altered = build_review_queue(replace(dataset, cases=[case]))["cases"][0]["case_sha256"]
    assert altered != original
