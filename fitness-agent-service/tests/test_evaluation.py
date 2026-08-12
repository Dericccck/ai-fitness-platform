from app.rag.evaluation import (
    RetrievalEvalCase,
    RetrievalEvalThresholds,
    aggregate_results,
    case_from_mapping,
    evaluate_case,
)
from app.rag.evaluation_cli import main


def test_retrieval_evaluation_calculates_recall_and_mrr() -> None:
    case = RetrievalEvalCase("case-1", "热身", frozenset({"doc-a", "doc-b"}))

    result = evaluate_case(case, ["doc-x", "doc-b", "doc-a"], k=3)

    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 0.5
    assert aggregate_results([result]) == {
        "recall_at_k": 1.0,
        "mrr": 0.5,
        "case_count": 1.0,
        "forbidden_hits": 0.0,
    }


def test_evaluation_deduplicates_hits_and_counts_forbidden_results() -> None:
    case = RetrievalEvalCase(
        "security-1",
        "组织训练",
        frozenset({"allowed-1", "allowed-2"}),
        frozenset({"other-org-1"}),
    )

    result = evaluate_case(case, ["allowed-1", "allowed-1", "other-org-1"], k=3)

    assert result.recall_at_k == 0.5
    assert result.forbidden_hits == 1


def test_thresholds_report_quality_and_permission_failures() -> None:
    thresholds = RetrievalEvalThresholds(0.9, 0.8)

    failures = thresholds.validate(
        {"recall_at_k": 0.5, "mrr": 0.7, "forbidden_hits": 1.0, "case_count": 1.0}
    )

    assert len(failures) == 3


def test_case_mapping_loads_golden_retrieval_and_forbidden_ids() -> None:
    case = case_from_mapping(
        {
            "case_id": "case-1",
            "query": "热身",
            "relevant_ids": ["allowed-1"],
            "forbidden_ids": ["private-1"],
            "retrieved_ids": ["allowed-1"],
        }
    )

    assert case.retrieved_ids == ("allowed-1",)
    assert case.forbidden_ids == frozenset({"private-1"})


def test_evaluation_cli_returns_nonzero_when_threshold_is_breached(tmp_path) -> None:
    cases = tmp_path / "cases.json"
    thresholds = tmp_path / "thresholds.json"
    cases.write_text(
        '[{"case_id":"case-1","query":"热身","relevant_ids":["allowed-1"],'
        '"retrieved_ids":["other-1"],"forbidden_ids":["other-1"]}]',
        encoding="utf-8",
    )
    thresholds.write_text(
        '{"k": 5, "min_recall_at_k": 1.0, "min_mrr": 1.0, "max_forbidden_hits": 0}',
        encoding="utf-8",
    )

    assert main(["--cases", str(cases), "--thresholds", str(thresholds)]) == 1
