"""Validate a previously obtained, versioned semantic judgment; never call a model."""

from __future__ import annotations

from typing import Any


def grade_semantic(expected: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Accept only structured judgments tied to a frozen rubric and available evidence."""
    if not isinstance(evidence, dict):
        return {"status": "UNKNOWN", "reason": "SEMANTIC_EVIDENCE_MISSING"}
    refs = evidence.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and ref for ref in refs)
    ):
        return {"status": "UNKNOWN", "reason": "SEMANTIC_EVIDENCE_MISSING"}
    judgment = evidence.get("judgment")
    if not isinstance(judgment, dict):
        return {"status": "UNKNOWN", "reason": "JUDGMENT_MISSING"}
    fields = {"metric_id", "score", "reason", "evidence_ids", "judge_model", "prompt_version"}
    if set(judgment) != fields:
        return {"status": "UNKNOWN", "reason": "INVALID_JUDGMENT"}
    score = judgment["score"]
    ids = judgment["evidence_ids"]
    allowed_ids = evidence.get("available_evidence_ids")
    mapping = expected.get("score_mapping")
    if (
        judgment["metric_id"] != expected.get("metric_id")
        or judgment["prompt_version"] != expected.get("prompt_version")
        or not all(
            isinstance(judgment[key], str) and judgment[key].strip()
            for key in ("metric_id", "reason", "judge_model", "prompt_version")
        )
        or not isinstance(score, int)
        or isinstance(score, bool)
        or score not in (0, 1, 2)
        or not isinstance(ids, list)
        or not ids
        or not all(isinstance(item, str) and item for item in ids)
        or not isinstance(allowed_ids, list)
        or not all(isinstance(item, str) for item in allowed_ids)
        or not set(ids).issubset(set(allowed_ids))
        or not isinstance(mapping, dict)
        or set(mapping) != {"0", "1", "2"}
        or any(item not in ("PASS", "FAIL", "UNKNOWN") for item in mapping.values())
    ):
        return {"status": "UNKNOWN", "reason": "INVALID_JUDGMENT"}
    return {
        "status": mapping[str(score)],
        "reason": "JUDGMENT_VALIDATED",
        "score": score,
        "evidence_ids": ids,
        "judge_model": judgment["judge_model"],
        "prompt_version": judgment["prompt_version"],
    }
