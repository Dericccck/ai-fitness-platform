"""Evaluate the actual deterministic first-stage router, not the whole Agent."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.agent.supervisor import classify_route

from .dataset import Dataset


def evaluate_routing(dataset: Dataset, *, split: str) -> dict[str, Any]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.split != split or case.primary_scenario != "routing":
            continue
        messages = [step.message for step in case.steps if step.type == "chat"]
        allowed = case.expected.allowed_routes
        result: dict[str, Any] = {
            "case_id": case.case_id,
            "subcategory": case.subcategory,
            "review_status": case.review.status,
            "allowed_routes": allowed,
        }
        if len(messages) != 1 or not messages[0] or not allowed:
            result.update(
                status="SKIPPED", reason="MISSING_INPUT_OR_ROUTE_LABEL", actual_route=None
            )
        else:
            try:
                actual = classify_route(messages[0])
            except Exception:  # noqa: BLE001 - preserve failed cases without exposing exception data
                result.update(status="ERROR", reason="ROUTER_EXCEPTION", actual_route=None)
            else:
                result.update(
                    status="PASS" if actual in allowed else "FAIL",
                    reason="ROUTE_OBSERVED",
                    actual_route=actual,
                )
        rows.append(result)
    counts = Counter(row["status"] for row in rows)
    decidable = counts["PASS"] + counts["FAIL"]
    return {
        "scope": "deterministic_first_stage_router_only",
        "source": "synthetic_cases",
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "split": split,
        "review": "draft cases are exploratory and cannot be used as release evidence",
        "counts": {status: counts[status] for status in ("PASS", "FAIL", "SKIPPED", "ERROR")},
        "decidable_pass_rate": counts["PASS"] / decidable if decidable else None,
        "planned_coverage": decidable / len(rows) if rows else None,
        "cases": rows,
    }
