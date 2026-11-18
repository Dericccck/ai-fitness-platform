"""Retrieval scores for observed results, with unanswerable cases separated."""

from __future__ import annotations

from typing import Any

from app.rag.evaluation import RetrievalEvalCase, evaluate_case


def grade_retrieval(expected: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Score real observed IDs only; an unanswerable query has no recall denominator."""
    answerable = expected.get("answerable")
    relevant = expected.get("relevant_ids")
    forbidden = expected.get("forbidden_ids", [])
    k = expected.get("k")
    if (
        not isinstance(answerable, bool)
        or not isinstance(relevant, list)
        or not all(isinstance(item, str) and item for item in relevant)
        or len(set(relevant)) != len(relevant)
        or not isinstance(forbidden, list)
        or not all(isinstance(item, str) and item for item in forbidden)
        or not isinstance(k, int)
        or isinstance(k, bool)
        or k < 1
        or (answerable and not relevant)
        or (not answerable and relevant)
    ):
        return {"status": "UNKNOWN", "reason": "INVALID_RETRIEVAL_CONTRACT"}
    if not isinstance(evidence, dict):
        return {"status": "UNKNOWN", "reason": "RETRIEVAL_EVIDENCE_MISSING"}
    refs = evidence.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and ref for ref in refs)
    ):
        return {"status": "UNKNOWN", "reason": "RETRIEVAL_EVIDENCE_MISSING"}
    ids = evidence.get("retrieved_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
        return {"status": "UNKNOWN", "reason": "RETRIEVAL_RESULT_MISSING"}
    # Unauthorized content anywhere in the observed retrieval is a risk, even
    # when it falls outside the cutoff used for relevance metrics.
    forbidden_hits = len(set(ids) & set(forbidden))
    if not answerable:
        # Separate refusal and fabricated-citation assessments are required elsewhere.
        return {
            "status": "FAIL" if forbidden_hits else "NA",
            "reason": "FORBIDDEN_RETRIEVAL" if forbidden_hits else "UNANSWERABLE_RECALL_NA",
            "metrics": {
                "recall_at_k": None,
                "hit_at_k": None,
                "reciprocal_rank": None,
                "forbidden_hits": forbidden_hits,
            },
        }
    result = evaluate_case(
        RetrievalEvalCase("task", "", frozenset(relevant), frozenset(forbidden)), ids, k=k
    )
    minimum = expected.get("min_recall_at_k", 1.0)
    if not isinstance(minimum, (float, int)) or isinstance(minimum, bool) or not 0 <= minimum <= 1:
        return {"status": "UNKNOWN", "reason": "INVALID_RETRIEVAL_CONTRACT"}
    passed = result.recall_at_k >= minimum and not forbidden_hits
    return {
        "status": "PASS" if passed else "FAIL",
        "reason": "RETRIEVAL_MATCH" if passed else "RETRIEVAL_MISMATCH",
        "metrics": {
            "recall_at_k": result.recall_at_k,
            "hit_at_k": int(result.reciprocal_rank > 0),
            "reciprocal_rank": result.reciprocal_rank,
            "forbidden_hits": forbidden_hits,
        },
    }
