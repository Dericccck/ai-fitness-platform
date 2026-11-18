"""Pair evaluation runs only when their frozen conditions are comparable."""

from __future__ import annotations

from collections import Counter
from typing import Any


class IncompatibleRuns(ValueError):
    """The two reports do not establish comparable evaluation conditions."""


def compare_runs(
    baseline_manifest: dict[str, Any],
    baseline_trials: list[dict[str, Any]],
    candidate_manifest: dict[str, Any],
    candidate_trials: list[dict[str, Any]],
    *,
    vary: str | None = None,
) -> dict[str, Any]:
    # These are conditions, not results. An absent value is not proof of equality.
    keys = (
        "mode",
        "adapter",
        "result_origin",
        "seed",
        "dataset_sha256",
        "dataset_version",
        "selection",
        "selected_case_ids",
        "fixture_sha256",
        "grader_version",
        "repeats",
    )
    variables = ("revision", "model", "prompt_version", "tool_schema_version", "index_version")
    if vary is not None and vary not in variables:
        raise IncompatibleRuns("invalid comparison variable")
    # A contract-only run does not invoke a model, prompt, tool schema or index.
    # Those fields are mandatory when they can influence an actual sandbox run.
    context = (
        ("revision",)
        if baseline_manifest.get("mode") == "contract"
        else (*variables, "environment")
    )
    if vary is not None and vary not in context:
        raise IncompatibleRuns("comparison variable is not available in this mode")

    def known(value: Any) -> bool:
        return (
            value is not None and value != "unknown" and value != "" and value != {} and value != []
        )

    for key in (*keys, *context):
        a, b = baseline_manifest.get(key), candidate_manifest.get(key)
        if not known(a) or not known(b) or (key != vary and a != b):
            raise IncompatibleRuns(f"missing or unequal comparison condition: {key}")
    if baseline_manifest["mode"] not in {"contract", "model_sandbox", "integration_sandbox"}:
        raise IncompatibleRuns("invalid comparison mode")
    for key in ("dataset_sha256", "fixture_sha256"):
        digest = baseline_manifest[key]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise IncompatibleRuns(f"invalid comparison digest: {key}")
    if vary is not None and baseline_manifest[vary] == candidate_manifest[vary]:
        raise IncompatibleRuns("declared comparison variable did not change")
    if not isinstance(baseline_manifest["seed"], int) or isinstance(
        baseline_manifest["seed"], bool
    ):
        raise IncompatibleRuns("invalid seed")

    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
        result: dict[tuple[str, int], dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise IncompatibleRuns("invalid trial")
            case_id, trial_index = row.get("case_id"), row.get("trial_index")
            if (
                not isinstance(case_id, str)
                or not case_id
                or not isinstance(trial_index, int)
                or isinstance(trial_index, bool)
            ):
                raise IncompatibleRuns("invalid trial identity")
            key = (case_id, trial_index)
            if key in result:
                raise IncompatibleRuns(f"duplicate trial: {key}")
            status = row.get("status")
            if not isinstance(status, str) or status not in {
                "PASS",
                "FAIL",
                "INCONCLUSIVE",
                "SKIPPED",
                "ERROR",
            }:
                raise IncompatibleRuns("invalid trial status")
            if row.get("seed") != baseline_manifest["seed"] + trial_index or isinstance(
                row.get("seed"), bool
            ):
                raise IncompatibleRuns("trial seed does not match manifest")
            if not isinstance(row.get("executed"), bool):
                raise IncompatibleRuns("missing trial execution state")
            assertions = row.get("assertions")
            if not isinstance(assertions, list) or not assertions:
                raise IncompatibleRuns("trial lacks assertions")
            assertion_ids = set()
            for item in assertions:
                if (
                    not isinstance(item, dict)
                    or not all(
                        isinstance(item.get(k), str) and item[k] for k in ("id", "rule", "phase")
                    )
                    or not isinstance(item.get("required"), bool)
                    or not isinstance(item.get("applicable"), bool)
                    or (item.get("risk") is not None and not isinstance(item["risk"], str))
                    or not isinstance(item.get("status"), str)
                    or item.get("status")
                    not in {"PASS", "FAIL", "UNKNOWN", "ERROR", "SKIPPED", "NOT_RUN", "NA"}
                    or (item.get("applicable") is False and item.get("status") != "NA")
                    or item["id"] in assertion_ids
                ):
                    raise IncompatibleRuns("invalid trial assertion")
                assertion_ids.add(item["id"])
            if status in {"PASS", "FAIL"}:
                if not row["executed"]:
                    raise IncompatibleRuns("decidable trial lacks execution or assertions")
                required = [
                    a
                    for a in assertions
                    if isinstance(a, dict)
                    and (a.get("required") or a.get("risk"))
                    and a.get("status") != "NA"
                ]
                if not required or len(assertions) != sum(isinstance(a, dict) for a in assertions):
                    raise IncompatibleRuns("decidable trial lacks required assertions")
                decisive = [a for a in required if a.get("status") == status]
                if not decisive or (
                    status == "PASS" and any(a.get("status") != "PASS" for a in required)
                ):
                    raise IncompatibleRuns("trial outcome contradicts assertions")
                for item in decisive:
                    refs = item.get("evidence_ref_sha256")
                    if (
                        not isinstance(refs, list)
                        or not refs
                        or not all(
                            isinstance(ref, str)
                            and len(ref) == 64
                            and all(ch in "0123456789abcdef" for ch in ref)
                            for ref in refs
                        )
                    ):
                        raise IncompatibleRuns("decidable assertion lacks evidence reference")
            result[key] = row
        return result

    selected = baseline_manifest["selected_case_ids"]
    if (
        not isinstance(selected, list)
        or not selected
        or not all(isinstance(case_id, str) and case_id for case_id in selected)
        or len(set(selected)) != len(selected)
    ):
        raise IncompatibleRuns("invalid selected case list")
    left, right = index(baseline_trials), index(candidate_trials)
    if any(case_id not in selected for case_id, _ in left.keys() | right.keys()):
        raise IncompatibleRuns("trial outside selection")
    cases: dict[str, dict[str, Any]] = {
        case_id: {"case_id": case_id, "trials": []} for case_id in sorted(selected)
    }
    for case_id, trial_index in sorted(left.keys() | right.keys()):
        record = cases.setdefault(case_id, {"case_id": case_id, "trials": []})
        a, b = left.get((case_id, trial_index)), right.get((case_id, trial_index))
        if a is not None and b is not None:

            def signature(row: dict[str, Any]) -> set[tuple[Any, ...]]:
                return {
                    tuple(
                        item.get(key)
                        for key in ("id", "rule", "phase", "required", "risk", "applicable")
                    )
                    for item in row["assertions"]
                }

            if signature(a) != signature(b):
                raise IncompatibleRuns("trial assertion contracts do not match")

        def comparability_reason(row: dict[str, Any] | None) -> str | None:
            # Some rules (notably Recall for unanswerable questions) legitimately
            # yield NA despite the case marking the assertion applicable. Reports
            # do not carry the original frozen parameters, so we cannot decide if
            # that exception applies. Preserve a proven FAIL, but don't compare a
            # claimed PASS with an unverified necessary NA as a completed task.
            if (
                row is not None
                and row["status"] == "PASS"
                and any(
                    item["applicable"]
                    and (item["required"] or item["risk"])
                    and item["status"] == "NA"
                    for item in row["assertions"]
                )
            ):
                return "APPLICABLE_NA_REQUIRES_DATASET_VERIFICATION"
            return None

        record["trials"].append(
            {
                "trial_index": trial_index,
                "baseline": a and a.get("status"),
                "candidate": b and b.get("status"),
                "baseline_comparability_reason": comparability_reason(a),
                "candidate_comparability_reason": comparability_reason(b),
            }
        )

    repeats = baseline_manifest["repeats"]
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise IncompatibleRuns("invalid repeat count")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < repeats
        for _, index in left.keys() | right.keys()
    ):
        raise IncompatibleRuns("trial index outside planned repeats")

    def case_status(trials: list[dict[str, Any]], side: str) -> str:
        statuses = [t[side] for t in trials]
        if "FAIL" in statuses:
            return "FAIL"
        if any(t[f"{side}_comparability_reason"] for t in trials):
            return "INCONCLUSIVE"
        if len(statuses) != repeats or {t["trial_index"] for t in trials} != set(range(repeats)):
            return "INCONCLUSIVE"
        return "PASS" if all(s == "PASS" for s in statuses) else "INCONCLUSIVE"

    counts: Counter[str] = Counter()
    for record in cases.values():
        before, after = (case_status(record["trials"], side) for side in ("baseline", "candidate"))
        if "INCONCLUSIVE" in (before, after):
            change = "incomparable"
        elif before == after:
            change = "unchanged"
        else:
            change = "improved" if after == "PASS" else "regressed"
        record.update(baseline=before, candidate=after, change=change)
        counts[change] += 1
    return {
        "baseline_run_id": baseline_manifest.get("run_id"),
        "candidate_run_id": candidate_manifest.get("run_id"),
        "conditions": {key: baseline_manifest[key] for key in (*keys, *context) if key != vary},
        "varied_condition": (
            {
                "name": vary,
                "baseline": baseline_manifest[vary],
                "candidate": candidate_manifest[vary],
            }
            if vary is not None
            else None
        ),
        "counts": {
            key: counts[key] for key in ("improved", "regressed", "unchanged", "incomparable")
        },
        "cases": list(cases.values()),
    }
