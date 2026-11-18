"""Planned denominators and missing evidence remain visible in RAG reports."""

import pytest

from app.evaluation.task_eval.retrieval_summary import summarize_retrieval


def plan(case_id, *, relevant=None, answerable=True, k=2, system_failure_is_task_failure=False):
    return {
        "case_id": case_id,
        "answerable": answerable,
        "relevant_ids": ["a", "b"] if relevant is None and answerable else (relevant or []),
        "k": k,
        "system_failure_is_task_failure": system_failure_is_task_failure,
    }


def observed(case_id, ids, *, refs=None):
    return {
        "case_id": case_id,
        "state": "OBSERVED",
        "retrieved_ids": ids,
        "evidence_refs": ["retrieval-trace"] if refs is None else refs,
    }


def test_planned_denominators_empty_and_nonanswerable_separate():
    plans = [
        plan("matched"),
        plan("empty"),
        plan("missing"),
        plan("unknown"),
        plan("skipped"),
        plan("env"),
        plan("error", system_failure_is_task_failure=True),
        plan("unanswerable", answerable=False),
    ]
    obs = [
        observed("matched", ["x", "a"]),
        observed("empty", []),
        observed("unknown", ["a"], refs=[]),
        {"case_id": "skipped", "state": "SKIPPED"},
        {"case_id": "env", "state": "PRECONDITION_ERROR"},
        {"case_id": "error", "state": "SYSTEM_ERROR", "evidence_refs": ["failure-trace"]},
        observed("unanswerable", []),
    ]
    report = summarize_retrieval(plans, obs)
    summary = report["overall_same_k"]
    assert summary["planned_answerable"] == 7
    assert summary["retrieval_observed"] == 2 and summary["empty_results"] == 1
    assert (
        summary["unknown"]
        == summary["skipped"]
        == summary["not_run"]
        == summary["precondition_errors"]
        == 1
    )
    assert summary["system_errors_with_evidence"] == 1
    assert summary["observation_coverage"] == {"numerator": 2, "denominator": 7, "value": 2 / 7}
    assert summary["recall_at_k"]["value"] == 0.25
    assert summary["hit_at_k"]["value"] == 0.5
    assert summary["mrr"]["value"] == 0.25
    assert report["unanswerable"]["planned"] == 1 and report["unanswerable"]["recall_at_k"] is None


def test_system_failure_requires_both_predefined_policy_and_evidence():
    plans = [plan("unbacked", system_failure_is_task_failure=True), plan("unapproved")]
    obs = [
        {"case_id": "unbacked", "state": "SYSTEM_ERROR"},
        {"case_id": "unapproved", "state": "SYSTEM_ERROR", "evidence_refs": ["trace"]},
    ]
    result = summarize_retrieval(plans, obs)["overall_same_k"]
    assert result["system_errors_with_evidence"] == 0 and result["unknown"] == 2
    assert result["recall_at_k"]["value"] is None and result["recall_at_k"]["denominator"] == 0


def test_different_k_are_never_combined():
    result = summarize_retrieval(
        [plan("a", k=1), plan("b", k=5)], [observed("a", ["a"]), observed("b", ["b"])]
    )
    assert result["overall_same_k"] is None
    assert set(result["by_k"]) == {"1", "5"}
    assert result["by_k"]["1"]["recall_at_k"]["value"] == 0.5


@pytest.mark.parametrize(
    "plans,obs",
    [
        ([plan("a"), plan("a")], []),
        ([plan("a", answerable=False, relevant=["a"])], []),
        ([plan("a", relevant=[])], []),
        ([plan("a")], [observed("x", [])]),
        ([plan("a")], [observed("a", []), observed("a", [])]),
        ([plan("a")], [{"case_id": "a", "state": "PASS"}]),
    ],
)
def test_invalid_plans_and_observations_rejected(plans, obs):
    with pytest.raises(ValueError):
        summarize_retrieval(plans, obs)
