"""Fail-closed offline release assessment with administrator-pinned signers.

The public entry point never accepts a caller-supplied verifier or trust root.
Missing deployment trust, signatures or other required evidence blocks release.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .dataset import Dataset

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_STATUSES = {"PASS", "FAIL", "UNKNOWN", "ERROR", "SKIPPED", "NOT_RUN", "NA"}


class GatePolicy(BaseModel):
    """Frozen gate requirements, set before evaluating a report."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    dataset_sha256: str
    dataset_version: str = Field(min_length=1)
    revision: str
    mode: str
    split: str
    repeats: int = Field(ge=1, le=100)
    min_trial_decision_coverage: float = Field(gt=0, le=1, allow_inf_nan=False)
    metric_coverage: dict[str, float] = Field(min_length=1)
    risk_coverage: dict[str, float] = Field(min_length=1)

    @field_validator("dataset_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256")
        return value

    @field_validator("revision")
    @classmethod
    def valid_revision(cls, value: str) -> str:
        if not _REVISION.fullmatch(value):
            raise ValueError("revision must be a full lowercase commit SHA")
        return value

    @field_validator("mode")
    @classmethod
    def release_mode(cls, value: str) -> str:
        if value not in {"model_sandbox", "integration_sandbox"}:
            raise ValueError("release mode must use a real sandbox")
        return value

    @field_validator("split")
    @classmethod
    def release_split(cls, value: str) -> str:
        if value != "test":
            raise ValueError("release requires a separately reviewed test split")
        return value

    @field_validator("metric_coverage", "risk_coverage")
    @classmethod
    def valid_thresholds(cls, values: dict[str, float]) -> dict[str, float]:
        if any(
            not key.strip()
            or isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or not 0 < value <= 1
            for key, value in values.items()
        ):
            raise ValueError("every named threshold must be finite and in (0, 1]")
        return values


def gate_policy_sha256(policy: GatePolicy) -> str:
    """Canonical policy hash; provenance must prove it was frozen before execution."""
    raw = json.dumps(
        policy.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class _EvidenceVerifier(Protocol):
    def verify_report(
        self,
        dataset: Dataset,
        manifest: dict[str, Any],
        trials: list[dict[str, Any]],
        policy: GatePolicy,
    ) -> bool: ...
    def verify_isolation(self, manifest: dict[str, Any]) -> bool: ...
    def verify_human_review(self, case: Any) -> bool: ...
    def verify_assertion(
        self, case: Any, trial: dict[str, Any], assertion: dict[str, Any]
    ) -> bool: ...


class _UnavailableVerifier:
    """No local assertions, booleans or strings can stand in for trusted evidence."""

    def verify_report(
        self,
        dataset: Dataset,
        manifest: dict[str, Any],
        trials: list[dict[str, Any]],
        policy: GatePolicy,
    ) -> bool:
        return False

    def verify_isolation(self, manifest: dict[str, Any]) -> bool:
        return False

    def verify_human_review(self, case: Any) -> bool:
        return False

    def verify_assertion(self, case: Any, trial: dict[str, Any], assertion: dict[str, Any]) -> bool:
        return False


def assess_release_gate(
    dataset: Dataset, manifest: dict[str, Any], trials: list[dict[str, Any]], policy: GatePolicy
) -> dict[str, Any]:
    """Assess using fixed system trust, failing closed if it is unavailable."""
    from .signed_evidence import system_verifier

    verifier: _EvidenceVerifier = system_verifier(manifest) or _UnavailableVerifier()
    return _assess(dataset, manifest, trials, policy, verifier)


def _verified(call: Any, *args: Any) -> bool:
    try:
        return call(*args) is True
    except Exception:  # noqa: BLE001 - missing/invalid evidence is not a pass
        return False


def _declared_unanswerable_recall(case: Any, assertion: Any) -> bool:
    """Recognize only a predeclared recall denominator of zero, never a risk NA."""
    if assertion.rule != "required_evidence_recalled" or assertion.risk is not None:
        return False
    parameters = assertion.parameters
    forbidden = parameters.get("forbidden_ids", [])
    k = parameters.get("k")
    if (
        parameters.get("answerable") is not False
        or parameters.get("relevant_ids") != []
        or not isinstance(forbidden, list)
        or any(not isinstance(item, str) or not item for item in forbidden)
        or type(k) is not int
        or k < 1
    ):
        return False
    return not forbidden or any(
        item.rule == "authorization_denied_no_leak"
        and item.required
        and item.applicable
        and bool(item.risk)
        for item in case.expected.assertions
    )


def _assess(
    dataset: Dataset,
    manifest: dict[str, Any],
    trials: list[dict[str, Any]],
    policy: GatePolicy,
    verifier: _EvidenceVerifier,
) -> dict[str, Any]:
    """Private rule engine; public entry chooses the system-pinned verifier."""
    blocked: list[str] = []
    failures: list[str] = []
    observations: dict[str, Any] = {}
    if dataset.sha256 != policy.dataset_sha256 or dataset.dataset_version != policy.dataset_version:
        blocked.append("DATASET_MISMATCH")
    selected = [case for case in dataset.cases if case.split == policy.split]
    scoped = [case for case in selected if case.delivery_scope == "in_scope"]
    selected_by_id = {case.case_id: case for case in selected}
    if not scoped or len(selected_by_id) != len(selected):
        blocked.append("EMPTY_OR_DUPLICATE_SCOPE")
    expected_manifest = {
        "schema_version": "1.0",
        "gate_policy_sha256": gate_policy_sha256(policy),
        "dataset_sha256": policy.dataset_sha256,
        "dataset_version": policy.dataset_version,
        "revision": policy.revision,
        "mode": policy.mode,
        "repeats": policy.repeats,
        "selection": {"split": policy.split, "scope": "all", "include_planned": True},
        "selected_case_ids": sorted(selected_by_id),
    }
    if not isinstance(manifest, dict):
        return {
            "status": "BLOCKED",
            "reasons": ["INVALID_MANIFEST"],
            "failures": [],
            "observations": {},
        }
    for key, expected in expected_manifest.items():
        actual = manifest.get(key)
        # Python considers True == 1; report identity fields must not coerce.
        invalid_selection = key == "selection" and (
            not isinstance(actual, dict) or type(actual.get("include_planned")) is not bool
        )
        if (
            actual != expected
            or (key == "repeats" and type(actual) is not int)
            or invalid_selection
        ):
            blocked.append(f"MANIFEST_MISMATCH:{key}")
    origin = manifest.get("result_origin")
    adapter = manifest.get("adapter")
    if (
        not isinstance(origin, str)
        or not origin
        or not isinstance(adapter, str)
        or not adapter
        or any(
            word in (origin + " " + adapter).lower()
            for word in ("synthetic", "mock", "stub", "contract", "unknown", "unsupported")
        )
    ):
        blocked.append("STUB_OR_UNKNOWN_ORIGIN")
    for key in (
        "run_id",
        "grader_version",
        "fixture_sha256",
        "environment",
        "model",
        "prompt_version",
        "tool_schema_version",
        "index_version",
    ):
        value = manifest.get(key)
        if (
            not isinstance(value, str)
            or not value
            or value.lower() in {"unknown", "none", "unverified"}
        ):
            blocked.append(f"MANIFEST_EVIDENCE_MISSING:{key}")
    fixture = manifest.get("fixture_sha256")
    if not isinstance(fixture, str) or not _DIGEST.fullmatch(fixture):
        blocked.append("INVALID_FIXTURE_DIGEST")
    report_verified = _verified(verifier.verify_report, dataset, manifest, trials, policy)
    isolation_verified = _verified(verifier.verify_isolation, manifest)
    if not report_verified:
        blocked.append("REPORT_PROVENANCE_UNVERIFIED")
    if not isolation_verified:
        blocked.append("ISOLATION_UNVERIFIED")
    for case in scoped:
        if case.review.status != "reviewed" or not _verified(verifier.verify_human_review, case):
            blocked.append(f"HUMAN_REVIEW_UNVERIFIED:{case.case_id}")
        if case.capability_status != "supported":
            blocked.append(f"CAPABILITY_NOT_SUPPORTED:{case.case_id}")
        if (
            case.primary_scenario == "safety"
            and case.subcategory == "unsafe_guidance"
            and not any(
                assertion.applicable
                and assertion.required
                and (
                    assertion.rule == "no_severe_unsafe_guidance"
                    or assertion.risk in {"unsafe_guidance", "unsafe_training", "medical"}
                )
                for assertion in case.expected.assertions
            )
        ):
            blocked.append(f"CLINICAL_ASSERTION_MISSING:{case.case_id}")
    if not isinstance(trials, list):
        return {
            "status": "BLOCKED",
            "reasons": [*blocked, "INVALID_TRIALS"],
            "failures": [],
            "observations": {},
        }
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    trial_ids: set[str] = set()
    for raw_trial in trials:
        if not isinstance(raw_trial, dict):
            blocked.append("INVALID_TRIAL")
            continue
        case_id, index, trial_id = (
            raw_trial.get("case_id"),
            raw_trial.get("trial_index"),
            raw_trial.get("trial_id"),
        )
        if (
            not isinstance(case_id, str)
            or type(index) is not int
            or not isinstance(trial_id, str)
            or not trial_id
        ):
            blocked.append("INVALID_TRIAL_IDENTITY")
            continue
        trial_key = (case_id, index)
        if trial_key in indexed or trial_id in trial_ids:
            blocked.append("DUPLICATE_TRIAL")
            continue
        indexed[trial_key] = raw_trial
        trial_ids.add(trial_id)
    expected_keys = {(case.case_id, index) for case in selected for index in range(policy.repeats)}
    if set(indexed) != expected_keys:
        blocked.append("TRIAL_SET_MISMATCH")
    metric_results: dict[str, list[str]] = defaultdict(list)
    risk_results: dict[str, list[str]] = defaultdict(list)
    decidable_trials = 0
    for case in scoped:
        for index in range(policy.repeats):
            maybe_trial = indexed.get((case.case_id, index))
            trial: dict[str, Any] = maybe_trial if maybe_trial is not None else {}
            status = trial.get("status")
            executed = trial.get("executed")
            trial_valid = maybe_trial is not None and executed is True
            if maybe_trial is None:
                blocked.append(f"TRIAL_NOT_RUN:{case.case_id}:{index}")
            else:
                for key in (
                    "family_id",
                    "split",
                    "primary_scenario",
                    "delivery_scope",
                    "capability_status",
                ):
                    if trial.get(key) != getattr(case, key):
                        trial_valid = False
                        blocked.append(f"TRIAL_CASE_MISMATCH:{case.case_id}:{index}:{key}")
                if (
                    not isinstance(status, str)
                    or status not in {"PASS", "FAIL", "INCONCLUSIVE", "SKIPPED", "ERROR"}
                    or type(executed) is not bool
                ):
                    trial_valid = False
                    blocked.append(f"INVALID_TRIAL_STATUS:{case.case_id}:{index}")
                elif status not in {"PASS", "FAIL"} or not executed:
                    blocked.append(f"TRIAL_INCOMPLETE:{case.case_id}:{index}")
            rows = trial.get("assertions")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                if maybe_trial is not None:
                    blocked.append(f"INVALID_ASSERTIONS:{case.case_id}:{index}")
                rows = []
            by_id = {row.get("id"): row for row in rows if isinstance(row.get("id"), str)}
            expected_ids = {a.id for a in case.expected.assertions}
            if len(by_id) != len(rows) or set(by_id) != expected_ids:
                trial_valid = False
                blocked.append(f"ASSERTION_SET_MISMATCH:{case.case_id}:{index}")
            per_metric: dict[str, list[str]] = defaultdict(list)
            per_risk: dict[str, list[str]] = defaultdict(list)
            required_statuses: list[str] = []
            for a in case.expected.assertions:
                row = by_id.get(a.id)
                declared_recall_na = _declared_unanswerable_recall(case, a)
                if row is None:
                    actual_status = "NOT_RUN"
                else:
                    actual_status = row.get("status")
                    contract_valid = True
                    for key in ("rule", "phase", "required", "risk", "applicable"):
                        if row.get(key) != getattr(a, key) or (
                            key in {"required", "applicable"} and type(row.get(key)) is not bool
                        ):
                            contract_valid = False
                            blocked.append(
                                f"ASSERTION_CONTRACT_MISMATCH:{case.case_id}:{index}:{a.id}:{key}"
                            )
                    if not isinstance(actual_status, str) or actual_status not in _STATUSES:
                        actual_status = "UNKNOWN"
                        blocked.append(f"INVALID_ASSERTION_STATUS:{case.case_id}:{index}:{a.id}")
                    if (
                        not a.applicable
                        and actual_status != "NA"
                        or a.applicable
                        and actual_status == "NA"
                        and not declared_recall_na
                    ):
                        blocked.append(
                            f"INVALID_ASSERTION_APPLICABILITY:{case.case_id}:{index}:{a.id}"
                        )
                    if (
                        a.applicable
                        and (a.required or a.risk)
                        and actual_status in {"PASS", "FAIL"}
                    ):
                        refs = row.get("evidence_ref_sha256")
                        refs_valid = not (
                            not isinstance(refs, list)
                            or not refs
                            or any(not isinstance(r, str) or not _DIGEST.fullmatch(r) for r in refs)
                        )
                        if not refs_valid:
                            blocked.append(
                                f"ASSERTION_EVIDENCE_MISSING:{case.case_id}:{index}:{a.id}"
                            )
                        evidence_verified = _verified(verifier.verify_assertion, case, trial, row)
                        if not evidence_verified:
                            blocked.append(
                                f"ASSERTION_EVIDENCE_UNVERIFIED:{case.case_id}:{index}:{a.id}"
                            )
                        if not (
                            refs_valid
                            and evidence_verified
                            and report_verified
                            and isolation_verified
                            and contract_valid
                            and trial_valid
                        ):
                            if actual_status == "FAIL":
                                blocked.append(
                                    f"REPORTED_FAILURE_UNVERIFIED:{case.case_id}:{index}:{a.id}"
                                )
                            actual_status = "UNKNOWN"
                if (
                    a.applicable
                    and (a.required or a.risk)
                    and not (actual_status == "NA" and declared_recall_na)
                ):
                    required_statuses.append(actual_status)
                    per_metric[a.rule].append(actual_status)
                    if a.risk:
                        per_risk[a.risk].append(actual_status)
                    if actual_status == "FAIL":
                        failures.append(f"REQUIRED_ASSERTION_FAILED:{case.case_id}:{index}:{a.id}")
                    elif actual_status != "PASS":
                        blocked.append(
                            f"REQUIRED_ASSERTION_INCOMPLETE:{case.case_id}:{index}:{a.id}"
                        )
            if not required_statuses:
                blocked.append(f"NO_REQUIRED_ASSERTIONS:{case.case_id}:{index}")
            if status == "FAIL":
                if (
                    report_verified
                    and isolation_verified
                    and executed
                    and "FAIL" in required_statuses
                ):
                    failures.append(f"TRIAL_FAILED:{case.case_id}:{index}")
                    decidable_trials += 1
                else:
                    blocked.append(f"TRIAL_FAILURE_UNVERIFIED:{case.case_id}:{index}")
            elif status == "PASS":
                if executed and required_statuses and all(s == "PASS" for s in required_statuses):
                    decidable_trials += 1
                else:
                    blocked.append(f"INCONSISTENT_TRIAL_PASS:{case.case_id}:{index}")
            for name, values in per_metric.items():
                metric_results[name].append(_reduce(values))
            for name, values in per_risk.items():
                risk_results[name].append(_reduce(values))
    planned_trials = len(scoped) * policy.repeats
    trial_coverage = decidable_trials / planned_trials if planned_trials else None
    observations["trial_coverage"] = {
        "numerator": decidable_trials,
        "denominator": planned_trials,
        "value": trial_coverage,
    }
    if trial_coverage is None or trial_coverage < policy.min_trial_decision_coverage:
        blocked.append("TRIAL_COVERAGE_BELOW_THRESHOLD")
    for name, results, thresholds in (
        ("metric", metric_results, policy.metric_coverage),
        ("risk", risk_results, policy.risk_coverage),
    ):
        if set(results) != set(thresholds):
            blocked.append(f"{name.upper()}_THRESHOLD_SET_MISMATCH")
        observations[f"{name}_coverage"] = {}
        for key in sorted(set(results) | set(thresholds)):
            values = results.get(key, [])
            applicable = len(values)
            decided = sum(s in {"PASS", "FAIL"} for s in values)
            coverage = decided / applicable if applicable else None
            observations[f"{name}_coverage"][key] = {
                "numerator": decided,
                "denominator": applicable,
                "value": coverage,
                "threshold": thresholds.get(key),
                "status_counts": {
                    status: values.count(status)
                    for status in ("PASS", "FAIL", "UNKNOWN", "ERROR", "SKIPPED", "NOT_RUN")
                },
            }
            if coverage is None or key not in thresholds or coverage < thresholds[key]:
                blocked.append(f"{name.upper()}_COVERAGE_INCOMPLETE:{key}")
    return {
        "status": "FAIL" if failures else "BLOCKED" if blocked else "PASS",
        "reasons": sorted(set(blocked)),
        "failures": sorted(set(failures)),
        "observations": observations,
    }


def _reduce(values: list[str]) -> str:
    return next(
        (
            status
            for status in ("FAIL", "ERROR", "SKIPPED", "NOT_RUN", "UNKNOWN", "PASS")
            if status in values
        ),
        "UNKNOWN",
    )
