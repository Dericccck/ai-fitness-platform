"""Offline retrieval evaluation primitives independent of model providers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalEvalCase:
    """One labeled query with expected authorized document or chunk IDs."""

    case_id: str
    query: str
    relevant_ids: frozenset[str]
    forbidden_ids: frozenset[str] = frozenset()
    retrieved_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalEvalResult:
    """Metrics for one evaluation case."""

    case_id: str
    recall_at_k: float
    reciprocal_rank: float
    forbidden_hits: int = 0


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
    unique_top_ids = set(top_ids)
    hits = len(unique_top_ids.intersection(case.relevant_ids))
    recall = hits / len(case.relevant_ids) if case.relevant_ids else 0.0
    reciprocal_rank = next(
        (
            1.0 / index
            for index, identifier in enumerate(top_ids, start=1)
            if identifier in case.relevant_ids
        ),
        0.0,
    )
    forbidden_hits = len(unique_top_ids.intersection(case.forbidden_ids))
    return RetrievalEvalResult(case.case_id, recall, reciprocal_rank, forbidden_hits)


def aggregate_results(results: Sequence[RetrievalEvalResult]) -> dict[str, float]:
    """Aggregate case-level metrics for CI thresholds and experiment comparison."""

    if not results:
        return {"recall_at_k": 0.0, "mrr": 0.0, "case_count": 0.0}
    return {
        "recall_at_k": sum(item.recall_at_k for item in results) / len(results),
        "mrr": sum(item.reciprocal_rank for item in results) / len(results),
        "case_count": float(len(results)),
        "forbidden_hits": float(sum(item.forbidden_hits for item in results)),
    }


@dataclass(frozen=True)
class RetrievalEvalThresholds:
    """Minimum quality and zero-tolerance security gates used by CI."""

    min_recall_at_k: float
    min_mrr: float
    max_forbidden_hits: int = 0

    def validate(self, metrics: dict[str, float]) -> list[str]:
        """Return actionable failures instead of silently accepting regressions."""

        failures: list[str] = []
        if metrics["recall_at_k"] < self.min_recall_at_k:
            failures.append(f"recall@k {metrics['recall_at_k']:.4f} < {self.min_recall_at_k:.4f}")
        if metrics["mrr"] < self.min_mrr:
            failures.append(f"mrr {metrics['mrr']:.4f} < {self.min_mrr:.4f}")
        if metrics["forbidden_hits"] > self.max_forbidden_hits:
            failures.append(
                f"forbidden hits {int(metrics['forbidden_hits'])} > {self.max_forbidden_hits}"
            )
        return failures


def case_from_mapping(data: dict[str, Any]) -> RetrievalEvalCase:
    """Load a JSON regression case with explicit authorized and forbidden IDs."""

    return RetrievalEvalCase(
        case_id=str(data["case_id"]),
        query=str(data["query"]),
        relevant_ids=frozenset(str(item) for item in data["relevant_ids"]),
        forbidden_ids=frozenset(str(item) for item in data.get("forbidden_ids", [])),
        retrieved_ids=tuple(str(item) for item in data.get("retrieved_ids", [])),
    )
