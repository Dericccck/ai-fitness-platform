"""Denominator-preserving retrieval metrics for predeclared single-trial cases.

This describes retrieval only. It does not score an agent answer, validate
labels, infer failure responsibility, or establish authorization coverage.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .graders.retrieval import grade_retrieval

_STATES = frozenset({"OBSERVED", "SYSTEM_ERROR", "PRECONDITION_ERROR", "UNKNOWN", "SKIPPED"})


def _mean(values: list[float]) -> dict[str, Any]:
    return {
        "value": sum(values) / len(values) if values else None,
        "numerator": sum(values),
        "denominator": len(values),
        "reason": None if values else "no observed answerable retrieval",
    }


def summarize_retrieval(
    plans: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarize planned cases without inventing scores for missing evidence.

    Plans require frozen IDs, answerability, relevant IDs and K. Observation
    `SYSTEM_ERROR` requires an explicit case policy and linked failure evidence;
    unclassified errors are UNKNOWN. Unique case IDs enforce case-level macro
    averaging; callers must summarize repeated trials separately.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for plan in plans:
        case_id = plan.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in by_id:
            raise ValueError("plan case IDs must be unique nonempty strings")
        answerable, relevant, k = plan.get("answerable"), plan.get("relevant_ids"), plan.get("k")
        if (
            type(answerable) is not bool
            or not isinstance(relevant, list)
            or any(not isinstance(item, str) or not item for item in relevant)
            or len(set(relevant)) != len(relevant)
            or bool(relevant) != answerable
            or type(k) is not int
            or k < 1
            or type(plan.get("system_failure_is_task_failure", False)) is not bool
        ):
            raise ValueError("invalid frozen retrieval plan")
        by_id[case_id] = plan
    observed: dict[str, dict[str, Any]] = {}
    for row in observations:
        case_id = row.get("case_id")
        if case_id not in by_id or case_id in observed or row.get("state") not in _STATES:
            raise ValueError("unexpected, duplicate or invalid retrieval observation")
        observed[case_id] = row
    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for case_id, plan in by_id.items():
        row = observed.get(case_id, {"state": "NOT_RUN"})
        state = row["state"]
        refs = row.get("evidence_refs")
        evidence = (
            isinstance(refs, list) and bool(refs) and all(isinstance(x, str) and x for x in refs)
        )
        ids = row.get("retrieved_ids")
        valid_ids = isinstance(ids, list) and all(isinstance(x, str) and x for x in ids)
        if state == "OBSERVED" and (not evidence or not valid_ids):
            state = "UNKNOWN"
        if state == "SYSTEM_ERROR" and (
            not evidence or not plan.get("system_failure_is_task_failure", False)
        ):
            state = "UNKNOWN"
        result: dict[str, Any] = {
            "case_id": case_id,
            "answerable": plan["answerable"],
            "k": plan["k"],
            "state": state,
            "empty_result": False,
            "recall_at_k": None,
            "hit_at_k": None,
            "reciprocal_rank": None,
        }
        if state == "OBSERVED":
            assert isinstance(ids, list)  # Invalid observations were changed to UNKNOWN above.
            result["empty_result"] = len(ids) == 0
            score = grade_retrieval(
                {
                    "answerable": plan["answerable"],
                    "relevant_ids": plan["relevant_ids"],
                    "k": plan["k"],
                },
                {"retrieved_ids": ids, "evidence_refs": refs},
            )
            if score["status"] == "UNKNOWN":
                result["state"] = "UNKNOWN"
            elif plan["answerable"]:
                metrics = score["metrics"]
                result.update(
                    recall_at_k=metrics["recall_at_k"],
                    hit_at_k=metrics["hit_at_k"],
                    reciprocal_rank=metrics["reciprocal_rank"],
                )
        rows.append(result)
        if plan["answerable"]:
            grouped[plan["k"]].append(result)

    def summary_for(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts = Counter(item["state"] for item in items)
        scored = [
            item
            for item in items
            if item["state"] == "OBSERVED" and item["recall_at_k"] is not None
        ]
        return {
            "planned_answerable": len(items),
            "retrieval_observed": len(scored),
            "empty_results": sum(item["empty_result"] for item in scored),
            "system_errors_with_evidence": counts["SYSTEM_ERROR"],
            "precondition_errors": counts["PRECONDITION_ERROR"],
            "unknown": counts["UNKNOWN"],
            "skipped": counts["SKIPPED"],
            "not_run": counts["NOT_RUN"],
            "observation_coverage": {
                "numerator": len(scored),
                "denominator": len(items),
                "value": len(scored) / len(items) if items else None,
            },
            "recall_at_k": _mean([item["recall_at_k"] for item in scored]),
            "hit_at_k": _mean([item["hit_at_k"] for item in scored]),
            "mrr": _mean([item["reciprocal_rank"] for item in scored]),
        }

    by_k = {str(k): summary_for(group) for k, group in sorted(grouped.items())}
    all_answerable = [item for item in rows if item["answerable"]]
    unanswerable = [item for item in rows if not item["answerable"]]
    return {
        "scope": "retrieval_component_only",
        "label_review": "NOT_ESTABLISHED",
        "by_k": by_k,
        "overall_same_k": summary_for(all_answerable) if len(grouped) <= 1 else None,
        "unanswerable": {
            "planned": len(unanswerable),
            "state_counts": dict(Counter(item["state"] for item in unanswerable)),
            "recall_at_k": None,
            "mrr": None,
        },
        "cases": rows,
    }
