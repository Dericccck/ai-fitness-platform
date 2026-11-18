"""Read and validate the versioned JSONL dataset without network or side effects."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import TaskCase

SCENARIO_QUOTAS = {
    "routing": {"dev": 13, "test": 7},
    "knowledge": {"dev": 20, "test": 10},
    "booking": {"dev": 23, "test": 12},
    "operations": {"dev": 14, "test": 6},
    "safety": {"dev": 10, "test": 5},
}
SUBCATEGORY_QUOTAS = {
    "routing": {"explicit_intent": 8, "clarification": 6, "multi_intent": 4, "role_mismatch": 2},
    "knowledge": {
        "answerable": 10,
        "unanswerable": 4,
        "expired_or_forbidden": 4,
        "training_memory": 6,
        "multimodal": 6,
    },
    "booking": {
        "positive": 10,
        "missing_or_ambiguous": 7,
        "reject": 4,
        "conflict": 4,
        "timeout_or_duplicate": 6,
        "reschedule_or_cancel": 4,
    },
    "operations": {"metrics": 8, "outreach": 8, "customer_service": 4},
    "safety": {
        "cross_tenant": 5,
        "unconfirmed_write": 4,
        "prompt_injection": 3,
        "unsafe_guidance": 3,
    },
}


class DatasetValidationError(ValueError):
    """An invalid dataset; messages include source line where available."""


@dataclass(frozen=True)
class Dataset:
    cases: list[TaskCase]
    sha256: str
    dataset_version: str
    coverage: dict[str, dict[str, Any]]
    warnings: list[str]


def _coverage(cases: list[TaskCase]) -> dict[str, dict[str, Any]]:
    by_split: dict[str, dict[str, Any]] = {}
    for split in ("dev", "test"):
        selected = [c for c in cases if c.split == split]
        by_split[split] = {
            "cases": len(selected),
            "scenario": dict(sorted(Counter(c.primary_scenario for c in selected).items())),
            "subcategory": {
                s: dict(
                    sorted(
                        Counter(c.subcategory for c in selected if c.primary_scenario == s).items()
                    )
                )
                for s in SCENARIO_QUOTAS
            },
            "tags": dict(sorted(Counter(tag for c in selected for tag in set(c.tags)).items())),
            "risk_level": dict(sorted(Counter(c.metadata.risk_level for c in selected).items())),
            "scenario_tags": {
                scenario: dict(
                    sorted(
                        Counter(
                            tag
                            for c in selected
                            if c.primary_scenario == scenario
                            for tag in set(c.tags)
                        ).items()
                    )
                )
                for scenario in SCENARIO_QUOTAS
            },
            "scope": dict(sorted(Counter(c.delivery_scope for c in selected).items())),
            "capability": dict(sorted(Counter(c.capability_status for c in selected).items())),
            "review": dict(sorted(Counter(c.review.status for c in selected).items())),
        }
    return by_split


def load_dataset(
    path: str | Path, *, require_reviewed_test: bool = False, check_quotas: bool = True
) -> Dataset:
    path = Path(path)
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DatasetValidationError(f"cannot read UTF-8 dataset: {exc}") from exc
    cases: list[TaskCase] = []
    errors: list[str] = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            errors.append(f"line {line_number}: blank JSONL line")
            continue
        try:
            cases.append(TaskCase.model_validate(json.loads(line)))
        except (ValueError, ValidationError) as exc:
            errors.append(f"line {line_number}: {exc}")
    if not cases:
        errors.append("dataset contains no valid cases")
    ids = Counter(c.case_id for c in cases)
    duplicates = sorted(k for k, v in ids.items() if v > 1)
    if duplicates:
        errors.append(f"duplicate case_id: {duplicates}")
    versions = {c.dataset_version for c in cases}
    if len(versions) != 1:
        errors.append(f"expected exactly one dataset_version, got {sorted(versions)}")
    families: dict[str, set[str]] = defaultdict(set)
    for c in cases:
        families[c.family_id].add(c.split)
        if c.subcategory not in SUBCATEGORY_QUOTAS[c.primary_scenario]:
            errors.append(
                f"{c.case_id}: unknown subcategory {c.subcategory!r} for {c.primary_scenario}"
            )
        if c.capability_status == "supported" and not c.fixture.namespace_isolated:
            errors.append(f"{c.case_id}: supported case fixture must be namespace-isolated")
        if require_reviewed_test and c.split == "test" and c.review.status != "reviewed":
            errors.append(f"{c.case_id}: test case is not reviewed")
    for family, family_splits in families.items():
        if len(family_splits) > 1:
            errors.append(f"family {family!r} crosses splits")
    coverage = _coverage(cases)
    if check_quotas:
        for scenario, splits in SCENARIO_QUOTAS.items():
            for split, count in splits.items():
                actual = coverage[split]["scenario"].get(scenario, 0)
                if actual != count:
                    errors.append(f"{scenario}/{split}: expected {count}, got {actual}")
            for subcategory, count in SUBCATEGORY_QUOTAS[scenario].items():
                actual = sum(
                    c.primary_scenario == scenario and c.subcategory == subcategory for c in cases
                )
                if actual != count:
                    errors.append(f"{scenario}/{subcategory}: expected {count}, got {actual}")
    if errors:
        raise DatasetValidationError("\n".join(errors))
    warnings = [
        "Family membership and split leakage require independent human review; automatic checks only verify declared IDs."
    ]
    draft_test = sum(c.split == "test" and c.review.status != "reviewed" for c in cases)
    if draft_test:
        warnings.append(
            f"{draft_test} test cases are draft planning data, not a frozen or release-eligible test set."
        )
    for split, values in coverage.items():
        missing_tags = sorted(
            {"positive", "negative", "boundary", "multi_step", "fault", "risk"}
            - set(values["tags"])
        )
        if missing_tags:
            warnings.append(f"{split} has no cases tagged: {', '.join(missing_tags)}")
    if any(c.capability_status == "planned" and c.delivery_scope == "in_scope" for c in cases):
        warnings.append(
            "In-scope planned cases remain in the denominator and block coverage acceptance when unexecutable."
        )
    return Dataset(cases, hashlib.sha256(raw).hexdigest(), next(iter(versions)), coverage, warnings)


def validate_dataset(
    path: str | Path, *, require_reviewed_test: bool = False, check_quotas: bool = True
) -> Dataset:
    return load_dataset(
        path, require_reviewed_test=require_reviewed_test, check_quotas=check_quotas
    )
