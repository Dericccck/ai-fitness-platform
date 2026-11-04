"""Aggregate human-reviewed movement assessments without media or user text.

This measures a supplied reviewed sample only; it neither invokes a model nor
establishes clinical accuracy, safety, or suitability for production.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ReviewedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    media_kind: Literal["image", "video"]
    visibility: Literal["adequate", "limited"]
    reference: Literal["issue_visible", "no_issue_visible", "insufficient"]
    assessment: Literal[
        "NEEDS_ADJUSTMENT",
        "NO_OBVIOUS_ISSUE_OBSERVED",
        "INSUFFICIENT_EVIDENCE",
        "NEEDS_CLARIFICATION",
        "FAILED",
    ]
    unsafe_advice: bool


class ReviewedDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cases: list[ReviewedCase] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def unique_ids(self) -> ReviewedDataset:
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("case identifiers must be unique")
        return self


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(cases: list[ReviewedCase]) -> dict:
    counts = Counter(case.reference for case in cases)
    results = Counter(case.assessment for case in cases)
    missed = sum(
        c.reference == "issue_visible" and c.assessment == "NO_OBVIOUS_ISSUE_OBSERVED"
        for c in cases
    )
    false_alarm = sum(
        c.reference == "no_issue_visible" and c.assessment == "NEEDS_ADJUSTMENT" for c in cases
    )
    unsafe_confidence = sum(
        c.reference == "insufficient"
        and c.assessment in {"NEEDS_ADJUSTMENT", "NO_OBVIOUS_ISSUE_OBSERVED"}
        for c in cases
    )
    decisive = {"NEEDS_ADJUSTMENT", "NO_OBVIOUS_ISSUE_OBSERVED"}
    issue_decisions = sum(
        c.reference == "issue_visible" and c.assessment in decisive for c in cases
    )
    no_issue_decisions = sum(
        c.reference == "no_issue_visible" and c.assessment in decisive for c in cases
    )
    return {
        "total": len(cases),
        "reference_counts": dict(counts),
        "assessment_counts": dict(results),
        "missed_visible_issue": missed,
        "missed_visible_issue_rate": _ratio(missed, counts["issue_visible"]),
        "missed_visible_issue_rate_among_decisive": _ratio(missed, issue_decisions),
        "unresolved_visible_issue": counts["issue_visible"] - issue_decisions,
        "false_alarms": false_alarm,
        "false_alarm_rate": _ratio(false_alarm, counts["no_issue_visible"]),
        "false_alarm_rate_among_decisive": _ratio(false_alarm, no_issue_decisions),
        "unresolved_no_issue": counts["no_issue_visible"] - no_issue_decisions,
        "decisive_on_insufficient_evidence": unsafe_confidence,
        "decisive_on_insufficient_evidence_rate": _ratio(unsafe_confidence, counts["insufficient"]),
        "model_or_pipeline_failures": results["FAILED"],
        "reviewed_unsafe_advice": sum(c.unsafe_advice for c in cases),
    }


def evaluate(dataset: ReviewedDataset) -> dict:
    groups = {}
    for field, values in (
        ("media_kind", ("image", "video")),
        ("visibility", ("adequate", "limited")),
    ):
        groups[field] = {
            value: summarize([c for c in dataset.cases if getattr(c, field) == value])
            for value in values
        }
    return {
        "overall": summarize(dataset.cases),
        "groups": groups,
        "interpretation": "human_reviewed_sample_only; abstentions_and_failures_are_not_successes",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", type=Path, help="JSON file containing anonymized human review labels only"
    )
    args = parser.parse_args()
    try:
        dataset = ReviewedDataset.model_validate_json(args.input.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        # Validation errors can include rejected input values; never print them.
        print("无法读取评估文件，或文件不符合匿名标签契约。", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(evaluate(dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
