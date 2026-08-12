"""Offline retrieval evaluation primitives independent of model providers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalEvalCase:
    """One labeled query with expected authorized document or chunk IDs."""

    case_id: str
    query: str
    relevant_ids: frozenset[str]


@dataclass(frozen=True)
class RetrievalEvalResult:
    """Metrics for one evaluation case."""

    case_id: str
    recall_at_k: float
    reciprocal_rank: float


def evaluate_case(
    case: RetrievalEvalCase,
    retrieved_ids: Sequence[str],
    *,
    k: int,
) -> RetrievalEvalResult:
    """Compute recall@k and MRR from stable IDs, not model-generated explanations."""

    if k < 1:
        raise ValueError("evaluation k must be positive")
    top_ids = list(retrieved_ids[:k])
    hits = sum(1 for identifier in top_ids if identifier in case.relevant_ids)
    recall = hits / len(case.relevant_ids) if case.relevant_ids else 0.0
    reciprocal_rank = next(
        (
            1.0 / index
            for index, identifier in enumerate(top_ids, start=1)
            if identifier in case.relevant_ids
        ),
        0.0,
    )
    return RetrievalEvalResult(case.case_id, recall, reciprocal_rank)


def aggregate_results(results: Sequence[RetrievalEvalResult]) -> dict[str, float]:
    """Aggregate case-level metrics for CI thresholds and experiment comparison."""

    if not results:
        return {"recall_at_k": 0.0, "mrr": 0.0, "case_count": 0.0}
    return {
        "recall_at_k": sum(item.recall_at_k for item in results) / len(results),
        "mrr": sum(item.reciprocal_rank for item in results) / len(results),
        "case_count": float(len(results)),
    }
