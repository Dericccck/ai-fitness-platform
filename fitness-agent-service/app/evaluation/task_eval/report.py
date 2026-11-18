"""Denominator-preserving reports for task evaluation runs."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

STATUSES = ("PASS", "FAIL", "UNKNOWN", "ERROR", "SKIPPED", "NOT_RUN", "NA")


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "reason": None if denominator else "zero denominator",
    }


def metric_summary(statuses: list[str]) -> dict[str, Any]:
    counts = {name: statuses.count(name) for name in STATUSES}
    planned = sum(counts[name] for name in STATUSES if name != "NA")
    decidable = counts["PASS"] + counts["FAIL"]
    executed = decidable + counts["UNKNOWN"]
    return {
        "counts": counts,
        "planned_applicable": planned,
        "pass_rate_decidable": ratio(counts["PASS"], decidable),
        "decision_coverage": ratio(decidable, planned),
        "executed_decision_coverage": ratio(decidable, executed),
        "planned_pass_fraction": ratio(counts["PASS"], planned),
    }


def reduce_trial(
    assertions: list[dict[str, Any]], *, executed: bool, failure: str | None = None
) -> str:
    """Risk or required failures survive later errors; missing evidence never passes."""
    if any(a["status"] == "FAIL" and (a.get("required") or a.get("risk")) for a in assertions):
        return "FAIL"
    if failure == "ERROR":
        # An error after execution started makes missing evidence inconclusive;
        # only a failure to prepare the environment is a pre-execution ERROR.
        return "INCONCLUSIVE" if executed else "ERROR"
    if failure == "SKIPPED":
        return "SKIPPED"
    required = [
        a for a in assertions if (a.get("required") or a.get("risk")) and a["status"] != "NA"
    ]
    if executed and any(
        a["status"] in {"UNKNOWN", "ERROR", "SKIPPED", "NOT_RUN"} for a in required
    ):
        return "INCONCLUSIVE"
    if executed and required and all(a["status"] == "PASS" for a in required):
        return "PASS"
    return "ERROR"


def reduce_case(trials: list[dict[str, Any]], repeats: int) -> str:
    if any(t["status"] == "FAIL" for t in trials):
        return "FAIL"
    indexes = [t.get("trial_index") for t in trials]
    if (
        repeats > 0
        and len(trials) == repeats
        and all(isinstance(index, int) and not isinstance(index, bool) for index in indexes)
        and set(indexes) == set(range(repeats))
        and all(t["status"] == "PASS" and t.get("executed") is True for t in trials)
    ):
        return "PASS"
    return "INCONCLUSIVE"


def summarize(trials: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    in_scope = [t for t in trials if t["delivery_scope"] == "in_scope"]
    cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scenario_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metric_trials: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    risk_trials: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for trial in in_scope:
        cases[trial["case_id"]].append(trial)
        scenario_trials[trial["primary_scenario"]].append(trial)
        for a in trial["assertions"]:
            metric_trials[(a["rule"], trial["case_id"], trial["trial_index"])].append(a)
            if a.get("risk"):
                risk_trials[(a["risk"], trial["case_id"], trial["trial_index"])].append(a)
    metrics: dict[str, list[str]] = defaultdict(list)
    for (rule, _case_id, _index), items in metric_trials.items():
        # One metric per case/trial. An applicable failure is never hidden by NA.
        applicable = [a for a in items if a["status"] != "NA"]
        relevant = [a for a in applicable if a.get("required") or a.get("risk")] or applicable
        order = ("FAIL", "ERROR", "SKIPPED", "NOT_RUN", "UNKNOWN", "PASS")
        status = next((s for s in order if any(a["status"] == s for a in relevant)), "NA")
        metrics[rule].append(status)
    risks: dict[str, list[str]] = defaultdict(list)
    for (risk, _case_id, _index), items in risk_trials.items():
        applicable = [a for a in items if a["status"] != "NA"]
        order = ("FAIL", "ERROR", "SKIPPED", "NOT_RUN", "UNKNOWN", "PASS")
        status = next((s for s in order if any(a["status"] == s for a in applicable)), "NA")
        risks[risk].append(status)
    case_results = []
    for case_id, rows in sorted(cases.items()):
        counts = dict(Counter(t["status"] for t in rows))
        case_results.append(
            {
                "case_id": case_id,
                "status": reduce_case(rows, repeats),
                "planned_trials": repeats,
                "executed_trials": sum(t["executed"] for t in rows),
                "trial_statuses": counts,
                "decidable_pass_rate": ratio(
                    counts.get("PASS", 0), counts.get("PASS", 0) + counts.get("FAIL", 0)
                ),
            }
        )
    trial_counts = dict(Counter(t["status"] for t in in_scope))
    case_counts = dict(Counter(c["status"] for c in case_results))
    return {
        "scope": {
            "in_scope_trials": len(in_scope),
            "out_of_scope_trials": len(trials) - len(in_scope),
            "planned_in_scope_trials": sum(t["capability_status"] == "planned" for t in in_scope),
            "supported_in_scope_trials": sum(
                t["capability_status"] == "supported" for t in in_scope
            ),
        },
        "trial_counts": trial_counts,
        "case_counts": case_counts,
        "cases": case_results,
        "trial_task_pass_rate_decidable": ratio(
            trial_counts.get("PASS", 0), trial_counts.get("PASS", 0) + trial_counts.get("FAIL", 0)
        ),
        "trial_decision_coverage": ratio(
            trial_counts.get("PASS", 0) + trial_counts.get("FAIL", 0), len(in_scope)
        ),
        "case_task_pass_rate_decidable": ratio(
            case_counts.get("PASS", 0), case_counts.get("PASS", 0) + case_counts.get("FAIL", 0)
        ),
        "case_decision_coverage": ratio(
            case_counts.get("PASS", 0) + case_counts.get("FAIL", 0), len(cases)
        ),
        "scenarios": {
            name: dict(Counter(t["status"] for t in rows))
            for name, rows in sorted(scenario_trials.items())
        },
        "metrics": {name: metric_summary(values) for name, values in sorted(metrics.items())},
        "risks": {
            name: {
                "violations": values.count("FAIL"),
                "decidable_exposures": values.count("PASS") + values.count("FAIL"),
                "unknown": values.count("UNKNOWN"),
                "status_counts": {status: values.count(status) for status in STATUSES},
            }
            for name, values in sorted(risks.items())
        },
    }


def markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    return (
        "# 离线任务评测报告\n\n"
        f"- 运行模式：{manifest['mode']}；适配器：{manifest['adapter']}\n"
        f"- 数据版本：{manifest['dataset_version']}；摘要：{manifest['dataset_sha256']}\n"
        f"- 范围内 Trial：{summary['scope']['in_scope_trials']}；范围外 Trial：{summary['scope']['out_of_scope_trials']}\n"
        f"- Trial 状态：{summary['trial_counts']}\n"
        f"- Case 状态：{summary['case_counts']}\n"
        f"- Trial 判定覆盖率：{summary['trial_decision_coverage']}\n\n"
        "详细分母、按指标统计和逐题结果见 summary.json 与 trials.jsonl。"
        "合成桩只验证评测契约，不能证明真实 Agent 表现。\n"
    )
