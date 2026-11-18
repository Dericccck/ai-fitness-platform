"""Exercise dataset-linked retrieval, fault detection and contract drift."""

from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.knowledge_dataset_local import evaluate_knowledge_dataset


@pytest.fixture
def dataset():
    path = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    return load_dataset(path)


@pytest.mark.asyncio
async def test_dataset_linked_retrieval_exposes_coverage_and_limits(dataset):
    result = await evaluate_knowledge_dataset(dataset)
    rows = {row["case_id"]: row for row in result["cases"]}
    assert len(rows) == 30
    assert sum(row["status"] == "OBSERVED" for row in rows.values()) == 18
    assert sum(row["status"] == "SKIPPED" for row in rows.values()) == 12
    assert rows["KNOW-001"]["retrieval"]["status"] == "PASS"
    assert rows["KNOW-011"]["retrieval"]["status"] == "NA"
    assert rows["KNOW-017"]["retrieval"]["status"] == "PASS"
    for case_id in ("KNOW-011", "KNOW-012", "KNOW-013", "KNOW-014"):
        assert rows[case_id]["relevant_document_ids"] == []
        assert len(rows[case_id]["fixture_manifest"]) == 1
        assert (
            rows[case_id]["fixture_manifest"][0]["content"]
            == "合成测试资料：和本问题无关的占位记录。"
        )
    for case_id in ("KNOW-015", "KNOW-016", "KNOW-017", "KNOW-018"):
        row = rows[case_id]
        assert set(row["forbidden_document_ids"]).isdisjoint(row["vector_candidate_ids"])
        assert set(row["forbidden_document_ids"]).isdisjoint(row["keyword_candidate_ids"])
        assert row["positive_control"]["status"] == "OBSERVED"
        assert row["acl"]["status"] == "UNKNOWN"
        assert row["evidence"]["actual"]["retrieved_ids"] == row["retrieved_ids"]
    assert all(row["answer"]["status"] == "UNKNOWN" for row in rows.values())


@pytest.mark.asyncio
async def test_filter_fault_is_caught_on_candidates_even_when_unanswerable(dataset):
    result = await evaluate_knowledge_dataset(dataset, bypass_filter=True)
    rows = {row["case_id"]: row for row in result["cases"]}
    for case_id in ("KNOW-015", "KNOW-016", "KNOW-017", "KNOW-018"):
        assert rows[case_id]["acl"]["status"] == "FAIL"
        assert rows[case_id]["status"] == "FAIL"
        assert rows[case_id]["forbidden_hits"] == rows[case_id]["forbidden_document_ids"]
        assert set(rows[case_id]["forbidden_document_ids"]) <= set(
            rows[case_id]["vector_candidate_ids"]
        )
        assert set(rows[case_id]["forbidden_document_ids"]) <= set(
            rows[case_id]["keyword_candidate_ids"]
        )


@pytest.mark.asyncio
async def test_changed_case_cannot_reuse_stale_fixture(dataset):
    target = next(case for case in dataset.cases if case.case_id == "KNOW-001")
    target.steps[0].message = "完全不同的问题"
    result = await evaluate_knowledge_dataset(dataset)
    row = next(row for row in result["cases"] if row["case_id"] == "KNOW-001")
    assert row["status"] == "SKIPPED"
    assert row["reason"] == "FIXTURE_CASE_MISMATCH"
