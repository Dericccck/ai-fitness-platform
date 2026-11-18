"""Summarize supplied, independently obtained human and Judge ratings.

This module does not collect ratings, authenticate reviewers, or perform model
calls. A report is descriptive evidence, never proof of blind review or a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .dataset import Dataset, load_dataset

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REF = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")


def _score(value: Any) -> bool:
    return value is None or (type(value) is int and value in (0, 1, 2))


def _report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def decided(row: dict[str, Any], side: str) -> bool:
        score = row[f"{side}_score"]
        return score is not None and not (row["rule"] == "no_severe_unsafe_guidance" and score == 1)

    comparable = [r for r in rows if decided(r, "human") and decided(r, "judge")]
    equal = sum(r["human_score"] == r["judge_score"] for r in comparable)
    matrix = {
        str(human): {
            str(judge): sum(
                r["human_score"] == human and r["judge_score"] == judge for r in comparable
            )
            for judge in range(3)
        }
        for human in range(3)
    }
    # For safety, a human score of 0 is a serious failure. Both a Judge score
    # of 1 and 2 fail to assign the same failing score, and are reported apart.
    human_fails = [r for r in comparable if r["human_score"] == 0]
    human_fails_judge_unknown = [
        r for r in rows if decided(r, "human") and r["human_score"] == 0 and not decided(r, "judge")
    ]
    return {
        "samples": len(rows),
        "comparable": len(comparable),
        "exact_agreement": {
            "numerator": equal,
            "denominator": len(comparable),
            "value": equal / len(comparable) if comparable else None,
        },
        "human_missing": sum(r["human_score"] is None for r in rows),
        "judge_missing": sum(r["judge_score"] is None for r in rows),
        "human_unknown": sum(not decided(r, "human") for r in rows),
        "judge_unknown": sum(not decided(r, "judge") for r in rows),
        "either_unknown": len(rows) - len(comparable),
        "confusion_matrix_human_by_judge": matrix,
        "human_failure_judge_score_1": sum(r["judge_score"] == 1 for r in human_fails),
        "human_failure_judge_score_2": sum(r["judge_score"] == 2 for r in human_fails),
        "human_failure_judge_unknown": len(human_fails_judge_unknown),
        "human_failure_comparable": len(human_fails),
    }


def summarize_calibration(dataset: Dataset, bundle: Any) -> dict[str, Any]:
    """Validate dataset-bound rating claims and summarize them, preserving nulls."""
    if not isinstance(bundle, dict) or set(bundle) != {
        "schema_version",
        "dataset_sha256",
        "rubric_ref",
        "judge_model",
        "prompt_version",
        "ratings",
    }:
        raise ValueError("invalid calibration bundle")
    if bundle["schema_version"] != "1.0" or bundle["dataset_sha256"] != dataset.sha256:
        raise ValueError("calibration dataset or schema mismatch")
    for field in ("rubric_ref", "judge_model", "prompt_version"):
        if not isinstance(bundle[field], str) or not _REF.fullmatch(bundle[field]):
            raise ValueError("invalid calibration version reference")
    ratings = bundle["ratings"]
    if not isinstance(ratings, list) or not ratings:
        raise ValueError("ratings are required")
    cases = {case.case_id: case for case in dataset.cases}
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for item in ratings:
        if not isinstance(item, dict) or set(item) != {
            "case_id",
            "metric_id",
            "human_score",
            "judge_score",
            "human_evidence_sha256",
            "judge_evidence_sha256",
        }:
            raise ValueError("invalid calibration rating")
        case_id, metric = item["case_id"], item["metric_id"]
        if not isinstance(case_id, str) or case_id not in cases or cases[case_id].split != "dev":
            raise ValueError("calibration is restricted to development cases")
        if not isinstance(metric, str) or not _REF.fullmatch(metric):
            raise ValueError("invalid metric")
        matching = [
            assertion
            for assertion in cases[case_id].expected.assertions
            if assertion.id == metric
            and assertion.applicable
            and assertion.rule in {"semantic", "no_severe_unsafe_guidance"}
        ]
        if not matching:
            raise ValueError("metric is not an applicable semantic assertion")
        key = (case_id, metric)
        if key in seen:
            raise ValueError("duplicate case and metric")
        seen.add(key)
        for side in ("human", "judge"):
            value, reference = item[f"{side}_score"], item[f"{side}_evidence_sha256"]
            if (
                not _score(value)
                or (value is None and reference is not None)
                or (
                    value is not None
                    and (not isinstance(reference, str) or not _DIGEST.fullmatch(reference))
                )
            ):
                raise ValueError("invalid rating or evidence reference")
        case = cases[case_id]
        rows.append(
            {
                **item,
                "scenario": case.primary_scenario,
                "risk_level": case.metadata.risk_level,
                "rule": matching[0].rule,
            }
        )
    metrics = sorted({(row["metric_id"], row["rule"]) for row in rows})
    risks = sorted({row["risk_level"] for row in rows})
    scenarios = sorted({row["scenario"] for row in rows})
    return {
        "schema_version": "1.0",
        "dataset_sha256": dataset.sha256,
        "dataset_version": dataset.dataset_version,
        "rubric_ref": bundle["rubric_ref"],
        "judge_model": bundle["judge_model"],
        "prompt_version": bundle["prompt_version"],
        "overall": _report(rows),
        "by_metric": {
            f"{metric}:{rule}": _report(
                [r for r in rows if r["metric_id"] == metric and r["rule"] == rule]
            )
            for metric, rule in metrics
        },
        "by_risk": {key: _report([r for r in rows if r["risk_level"] == key]) for key in risks},
        "by_scenario": {
            key: _report([r for r in rows if r["scenario"] == key]) for key in scenarios
        },
        "limitations": [
            "Scores and evidence digests are supplied claims; reviewer identity, review independence and evidence authenticity are not verified.",
            "This descriptive report does not certify calibration or approve a release.",
            "Risk summaries do not replace inspection of each serious error.",
            "A score of 1 is unknown for safety guidance; other semantic rubrics may treat 1 as a valid partial score.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--ratings", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = Path(args.ratings).read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("calibration bundle too large")
    result = summarize_calibration(load_dataset(args.dataset), json.loads(raw))
    result["ratings_sha256"] = hashlib.sha256(raw).hexdigest()
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {"samples": result["overall"]["samples"], "comparable": result["overall"]["comparable"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
