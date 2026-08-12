from app.rag.evaluation import (
    RetrievalEvalCase,
    aggregate_results,
    evaluate_case,
)


def test_retrieval_evaluation_calculates_recall_and_mrr() -> None:
    case = RetrievalEvalCase("case-1", "热身", frozenset({"doc-a", "doc-b"}))

    result = evaluate_case(case, ["doc-x", "doc-b", "doc-a"], k=3)

    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 0.5
    assert aggregate_results([result]) == {
        "recall_at_k": 1.0,
        "mrr": 0.5,
        "case_count": 1.0,
    }
