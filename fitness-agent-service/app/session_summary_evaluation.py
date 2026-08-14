"""短期会话摘要的确定性安全与格式离线评测。

该评测不调用 LLM，也不使用真实用户数据，只验证模型输出经过本地脱敏和结构化校验后
能否满足上线门禁。它不能证明摘要事实正确；语义质量需要另行建立人工标注集和模型评审
流程，本命令只负责把凭证泄露、空结果、超长和结构破坏拦在 CI。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .session_summary import SessionSummaryPayload, _sanitize_summary

_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(x-agent-context|authorization|api[_ -]?key|password|secret|token|confirmation[_ -]?id)"
    r"\s*[:=：]\s*(?!\[已脱敏\])[^\s，。；;]+"
    r"|\b(?:sk-|ghp_|gsk_)[A-Za-z0-9_-]{16,}\b"
)


@dataclass(frozen=True)
class SessionSummaryEvalCase:
    """一条脱敏后的模型输出样本。"""

    case_id: str
    model_output: str
    required_terms: tuple[str, ...] = ()
    expect_redaction: bool = False


@dataclass(frozen=True)
class SessionSummaryEvalResult:
    """一条摘要安全评测结果。"""

    case_id: str
    passed: bool
    invalid_json: bool
    empty_summary: bool
    oversize: bool
    credential_leaks_after_sanitize: int
    missing_required_terms: int
    redaction_applied: bool
    redaction_miss: bool


@dataclass(frozen=True)
class SessionSummaryEvalThresholds:
    """摘要输出进入 CI 的最低通过率和零容忍安全门禁。"""

    max_summary_chars: int = 3000
    min_pass_rate: float = 1.0
    max_invalid_cases: int = 0
    max_empty_cases: int = 0
    max_oversize_cases: int = 0
    max_credential_leaks_after_sanitize: int = 0
    max_missing_required_terms: int = 0
    max_redaction_misses: int = 0

    def validate(self, metrics: dict[str, float]) -> list[str]:
        """返回可执行失败原因，不把异常输出静默当成合格摘要。"""

        failures: list[str] = []
        checks = (
            ("pass_rate", metrics["pass_rate"], self.min_pass_rate, "<"),
            ("invalid_cases", metrics["invalid_cases"], self.max_invalid_cases, ">"),
            ("empty_cases", metrics["empty_cases"], self.max_empty_cases, ">"),
            ("oversize_cases", metrics["oversize_cases"], self.max_oversize_cases, ">"),
            (
                "credential_leaks_after_sanitize",
                metrics["credential_leaks_after_sanitize"],
                self.max_credential_leaks_after_sanitize,
                ">",
            ),
            (
                "missing_required_terms",
                metrics["missing_required_terms"],
                self.max_missing_required_terms,
                ">",
            ),
            ("redaction_misses", metrics["redaction_misses"], self.max_redaction_misses, ">"),
        )
        for name, actual, threshold, operator in checks:
            breached = actual < threshold if operator == "<" else actual > threshold
            if breached:
                failures.append(f"{name} {actual:.4f} {operator} {threshold:.4f}")
        return failures


def evaluate_case(
    case: SessionSummaryEvalCase, *, max_summary_chars: int
) -> SessionSummaryEvalResult:
    """执行一条不调用模型的摘要后处理门禁。"""

    try:
        payload = SessionSummaryPayload.model_validate(json.loads(case.model_output))
    except (json.JSONDecodeError, TypeError, ValidationError):
        return SessionSummaryEvalResult(case.case_id, False, True, False, False, 0, 0, False, False)

    raw_summary = payload.summary.strip()
    sanitized = _sanitize_summary(raw_summary, max_summary_chars)
    empty_summary = not bool(sanitized)
    oversize = len(raw_summary) > max_summary_chars
    credential_leaks = len(_CREDENTIAL_PATTERN.findall(sanitized))
    missing_terms = sum(term not in sanitized for term in case.required_terms)
    redaction_applied = sanitized != raw_summary
    redaction_misses = int(case.expect_redaction and not redaction_applied)
    passed = not (
        empty_summary or oversize or credential_leaks or missing_terms or redaction_misses
    )
    return SessionSummaryEvalResult(
        case.case_id,
        passed,
        False,
        empty_summary,
        oversize,
        credential_leaks,
        missing_terms,
        redaction_applied,
        bool(redaction_misses),
    )


def aggregate_results(results: Sequence[SessionSummaryEvalResult]) -> dict[str, float]:
    """汇总样本结果，供 CLI 和 CI 阈值判断。"""

    if not results:
        return {
            "case_count": 0.0,
            "passed_cases": 0.0,
            "pass_rate": 0.0,
            "invalid_cases": 0.0,
            "empty_cases": 0.0,
            "oversize_cases": 0.0,
            "credential_leaks_after_sanitize": 0.0,
            "missing_required_terms": 0.0,
            "redaction_misses": 0.0,
            "redaction_cases": 0.0,
        }
    passed = sum(result.passed for result in results)
    return {
        "case_count": float(len(results)),
        "passed_cases": float(passed),
        "pass_rate": passed / len(results),
        "invalid_cases": float(sum(result.invalid_json for result in results)),
        "empty_cases": float(sum(result.empty_summary for result in results)),
        "oversize_cases": float(sum(result.oversize for result in results)),
        "credential_leaks_after_sanitize": float(
            sum(result.credential_leaks_after_sanitize for result in results)
        ),
        "missing_required_terms": float(sum(result.missing_required_terms for result in results)),
        "redaction_misses": float(sum(result.redaction_miss for result in results)),
        "redaction_cases": float(sum(result.redaction_applied for result in results)),
    }


def case_from_mapping(data: dict[str, Any]) -> SessionSummaryEvalCase:
    """把 JSON 样本转换为固定结构，拒绝隐式读取其他字段。"""

    return SessionSummaryEvalCase(
        case_id=str(data["case_id"]),
        model_output=str(data["model_output"]),
        required_terms=tuple(str(item) for item in data.get("required_terms", [])),
        expect_redaction=bool(data.get("expect_redaction", False)),
    )


def main(argv: list[str] | None = None) -> int:
    """运行摘要安全门禁并返回 CI 兼容退出码。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args(argv)

    cases = _load_json(args.cases)
    threshold_data = _load_json(args.thresholds)
    thresholds = SessionSummaryEvalThresholds(
        max_summary_chars=int(threshold_data.get("max_summary_chars", 3000)),
        min_pass_rate=float(threshold_data.get("min_pass_rate", 1.0)),
        max_invalid_cases=int(threshold_data.get("max_invalid_cases", 0)),
        max_empty_cases=int(threshold_data.get("max_empty_cases", 0)),
        max_oversize_cases=int(threshold_data.get("max_oversize_cases", 0)),
        max_credential_leaks_after_sanitize=int(
            threshold_data.get("max_credential_leaks_after_sanitize", 0)
        ),
        max_missing_required_terms=int(threshold_data.get("max_missing_required_terms", 0)),
        max_redaction_misses=int(threshold_data.get("max_redaction_misses", 0)),
    )
    results = [
        evaluate_case(case_from_mapping(item), max_summary_chars=thresholds.max_summary_chars)
        for item in cases
    ]
    metrics = aggregate_results(results)
    failures = thresholds.validate(metrics)
    output = {
        "metrics": metrics,
        "thresholds": threshold_data,
        "cases": [result.__dict__ for result in results],
        "passed": not failures,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load evaluation file {path}: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
