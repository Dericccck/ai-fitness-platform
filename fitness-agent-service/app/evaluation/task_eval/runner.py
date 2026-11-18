"""Offline, fail-closed task runner.

Only an in-memory synthetic adapter is executable. Real sandbox modes are
explicitly unsupported until isolation and independent observation are proven.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adapters.synthetic import SyntheticAdapter
from .graders.rules import grade_rule
from .report import markdown, reduce_trial, summarize

MODES = ("contract", "model_sandbox", "integration_sandbox")
GRADER_VERSION = "task-eval-rules-v1"


def _revision() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=2
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _assertion_row(assertion: Any, status: str, reason: str) -> dict[str, Any]:
    return {
        "id": assertion.id,
        "rule": assertion.rule,
        "phase": assertion.phase,
        "required": assertion.required,
        "risk": assertion.risk,
        "applicable": assertion.applicable,
        "status": status,
        "reason": reason,
    }


def _empty_assertions(case: Any, status: str, reason: str) -> list[dict[str, Any]]:
    return [
        _assertion_row(
            a, status if a.applicable else "NA", reason if a.applicable else "NOT_APPLICABLE"
        )
        for a in case.expected.assertions
    ]


def run_trial(
    case: Any, index: int, seed: int, mode: str, adapter: SyntheticAdapter | None
) -> dict[str, Any]:
    started = time.monotonic()
    trial_id = uuid4().hex
    row: dict[str, Any] = {
        "trial_id": trial_id,
        "case_id": case.case_id,
        "family_id": case.family_id,
        "split": case.split,
        "primary_scenario": case.primary_scenario,
        "delivery_scope": case.delivery_scope,
        "capability_status": case.capability_status,
        "trial_index": index,
        "seed": seed,
        "executed": False,
        "status": "SKIPPED",
        "reason": None,
        "assertions": [],
        "duration_ms": None,
    }
    reason = None
    if case.delivery_scope != "in_scope":
        reason = "OUT_OF_SCOPE"
    elif case.review.status != "reviewed":
        reason = "CASE_NOT_REVIEWED"
    elif case.capability_status != "supported":
        reason = "CAPABILITY_PLANNED"
    elif mode != "contract" or adapter is None or not adapter.supports(case):
        reason = "UNSUPPORTED_ADAPTER_OR_FIXTURE"
    elif case.clock.mode == "injected":
        reason = "CLOCK_UNAVAILABLE"
    if reason:
        row.update(reason=reason, assertions=_empty_assertions(case, "SKIPPED", reason))
        row["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        return row

    assertions: dict[str, dict[str, Any]] = {}
    failure = None
    try:
        assert adapter is not None
        adapter.start(case, trial_id)
        row["executed"] = True
        for step in case.steps:
            adapter.step(step)
            if step.type == "assert":
                evidence = adapter.observe(step.phase)
                for a in case.expected.assertions:
                    if a.phase != step.phase or a.id in assertions:
                        continue
                    result = grade_rule(a.rule, a.parameters, evidence, a.applicable)
                    status = result.get("status")
                    unanswerable_recall = (
                        a.rule == "required_evidence_recalled"
                        and a.parameters.get("answerable") is False
                        and result.get("reason") == "UNANSWERABLE_RECALL_NA"
                    )
                    if status not in {"PASS", "FAIL", "UNKNOWN", "NA"} or (
                        a.applicable and status == "NA" and not unanswerable_recall
                    ):
                        status = "UNKNOWN"
                    item = _assertion_row(a, status, result.get("reason", "INVALID_GRADER_RESULT"))
                    item["grader_version"] = GRADER_VERSION
                    # Fingerprints let reviewers correlate synthetic references without
                    # copying raw references, answers, credentials, or payloads.
                    refs = evidence.get("evidence_refs") if isinstance(evidence, dict) else None
                    if isinstance(refs, list) and all(isinstance(ref, str) and ref for ref in refs):
                        item["evidence_ref_sha256"] = [
                            hashlib.sha256(ref.encode()).hexdigest() for ref in refs
                        ]
                        item["evidence_origin"] = "synthetic"
                    metrics = result.get("metrics")
                    if isinstance(metrics, dict):
                        item["metrics"] = {
                            k: v
                            for k, v in metrics.items()
                            if isinstance(k, str)
                            and (
                                v is None
                                or (
                                    isinstance(v, (int, float))
                                    and not isinstance(v, bool)
                                    and math.isfinite(v)
                                )
                            )
                        }
                    assertions[a.id] = item
    except Exception:  # noqa: BLE001 - preserve prior findings without leaking exception data
        # Do not leak exception messages, credentials, or raw evidence to output.
        failure = "ERROR"
        row["reason"] = "ADAPTER_OR_GRADER_ERROR"
    finally:
        try:
            if adapter is not None:
                adapter.close()
        except Exception:  # noqa: BLE001 - preserve prior findings without leaking exception data
            failure = "ERROR"
            row["reason"] = "CLEANUP_ERROR"
    for a in case.expected.assertions:
        if a.id not in assertions:
            status = (
                "NA"
                if not a.applicable
                else "ERROR"
                if failure and not row["executed"]
                else "UNKNOWN"
            )
            assertions[a.id] = _assertion_row(
                a, status, "NOT_APPLICABLE" if not a.applicable else "EVIDENCE_NOT_OBSERVED"
            )
    row["assertions"] = [assertions[a.id] for a in case.expected.assertions]
    row["status"] = reduce_trial(row["assertions"], executed=row["executed"], failure=failure)
    if row["status"] == "ERROR" and not row["reason"]:
        row["reason"] = "INVALID_CONTRACT"
    row["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return row


def execute(
    dataset: Any,
    *,
    split: str,
    mode: str,
    repeats: int = 1,
    seed: int = 0,
    adapter: SyntheticAdapter | None = None,
    dataset_path: str = "unknown",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if split not in ("dev", "test") or mode not in MODES or not 1 <= repeats <= 100:
        raise ValueError("invalid split, mode, or repeats (1–100)")
    if adapter is not None and (mode != "contract" or type(adapter) is not SyntheticAdapter):
        raise ValueError(
            "only the built-in in-memory synthetic adapter is permitted in contract mode"
        )
    selected = [case for case in dataset.cases if case.split == split]
    if not selected:
        raise ValueError("selection contains no cases")
    # Fixture identities, not data or evidence; unknown unless a real verified
    # fixture implementation exists. Prevents spurious comparable-run claims.
    manifest = {
        "schema_version": "1.0",
        "run_id": uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "dataset_path": dataset_path,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "revision": _revision(),
        "selection": {"split": split, "scope": "all", "include_planned": True},
        "selected_case_ids": sorted(c.case_id for c in selected),
        "fixture_sha256": "unknown",
        "grader_version": GRADER_VERSION,
        "repeats": repeats,
        "seed": seed,
        "adapter": adapter.name if adapter else "unsupported",
        "result_origin": "synthetic_contract" if adapter else "no_adapter",
        "model": "unknown",
        "prompt_version": "unknown",
        "tool_schema_version": "unknown",
        "index_version": "unknown",
        "environment": "in_memory" if adapter else "unknown",
    }
    trials = [
        run_trial(case, i, seed + i, mode, adapter) for case in selected for i in range(repeats)
    ]
    summary = summarize(trials, repeats)
    counts = summary["trial_counts"]
    manifest["status"] = (
        "FAIL"
        if counts.get("FAIL", 0)
        else "INCOMPLETE"
        if any(counts.get(s, 0) for s in ("SKIPPED", "ERROR", "INCONCLUSIVE"))
        or not counts.get("PASS", 0)
        else "PASS"
    )
    return manifest, trials, summary


def write_report(
    directory: str | Path,
    manifest: dict[str, Any],
    trials: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    names = ("manifest.json", "trials.jsonl", "summary.json", "report.md")
    if any((path / name).exists() for name in names):
        raise ValueError("output directory already contains a report")

    def save(name: str, content: str) -> None:
        with (path / name).open("x", encoding="utf-8") as stream:
            stream.write(content)

    save("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    save("trials.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in trials))
    save("summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    save("report.md", markdown(manifest, summary))


def exit_code(manifest: dict[str, Any]) -> int:
    return {"PASS": 0, "FAIL": 2, "INCOMPLETE": 3}[manifest["status"]]
