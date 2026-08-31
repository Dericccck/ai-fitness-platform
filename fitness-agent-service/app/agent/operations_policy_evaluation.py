"""经营查询前策略校验的确定性离线评测。

评测验证自然语言意图和模型工具参数之间的安全边界，不访问 Java Gateway、数据库或
LLM。它与 RAG/趋势评测不同：这里关注的是“是否允许执行查询”，而不是查询结果内容。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .operations_tools import OperationsMetricToolInput, validate_operations_query_policy


@dataclass(frozen=True)
class OperationsPolicyEvalCase:
    """一条自然语言问题与工具参数的一致性用例。"""

    case_id: str
    user_message: str
    query: dict[str, Any]
    today: date
    allowed_organization_ids: frozenset[str]
    expected_allowed: bool
    expected_reason_code: str


@dataclass(frozen=True)
class OperationsPolicyEvalResult:
    """单条策略评测结果。"""

    case_id: str
    passed: bool
    failures: tuple[str, ...]
    invalid: bool = False


def evaluate_case(case: OperationsPolicyEvalCase) -> OperationsPolicyEvalResult:
    """执行真实策略函数，确保不一致参数在业务调用前被拒绝。"""

    try:
        query = OperationsMetricToolInput.model_validate(case.query)
        decision = validate_operations_query_policy(
            case.user_message,
            query,
            today=case.today,
            allowed_organization_ids=case.allowed_organization_ids,
        )
    except (TypeError, ValueError) as exc:
        return OperationsPolicyEvalResult(
            case.case_id,
            False,
            (f"结果无效：{exc}",),
            invalid=True,
        )

    failures: list[str] = []
    if decision.allowed != case.expected_allowed:
        failures.append(f"allowed {decision.allowed} != {case.expected_allowed}")
    if decision.reason_code != case.expected_reason_code:
        failures.append(f"原因码 reason_code {decision.reason_code} != {case.expected_reason_code}")
    return OperationsPolicyEvalResult(case.case_id, not failures, tuple(failures))


def aggregate_results(results: list[OperationsPolicyEvalResult]) -> dict[str, float]:
    """汇总策略评测通过率和非法用例数量。"""

    if not results:
        return {"case_count": 0.0, "pass_rate": 0.0, "failed_cases": 0.0, "invalid_cases": 0.0}
    return {
        "case_count": float(len(results)),
        "pass_rate": sum(result.passed for result in results) / len(results),
        "failed_cases": float(sum(not result.passed for result in results)),
        "invalid_cases": float(sum(result.invalid for result in results)),
    }


def main(argv: list[str] | None = None) -> int:
    """运行查询前策略门禁并输出机器可读 JSON。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args(argv)
    cases_data = _load_json(args.cases)
    threshold_data = _load_json(args.thresholds)
    results: list[OperationsPolicyEvalResult] = []
    for item in cases_data:
        try:
            results.append(
                evaluate_case(
                    OperationsPolicyEvalCase(
                        case_id=str(item["case_id"]),
                        user_message=str(item["user_message"]),
                        query=dict(item["query"]),
                        today=date.fromisoformat(str(item["today"])),
                        allowed_organization_ids=frozenset(
                            str(value) for value in item.get("allowed_organization_ids", [])
                        ),
                        expected_allowed=bool(item["expected_allowed"]),
                        expected_reason_code=str(item["expected_reason_code"]),
                    )
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            case_id = str(item.get("case_id", "unknown")) if isinstance(item, dict) else "unknown"
            results.append(
                OperationsPolicyEvalResult(
                    case_id,
                    False,
                    (f"案例无效：{exc}",),
                    invalid=True,
                )
            )
    metrics = aggregate_results(results)
    failures: list[str] = []
    min_pass_rate = float(threshold_data["min_pass_rate"])
    max_failed_cases = int(threshold_data.get("max_failed_cases", 0))
    max_invalid_cases = int(threshold_data.get("max_invalid_cases", 0))
    if metrics["pass_rate"] < min_pass_rate:
        failures.append(f"通过率 pass_rate {metrics['pass_rate']:.4f} < {min_pass_rate:.4f}")
    if metrics["failed_cases"] > max_failed_cases:
        failures.append(
            f"失败案例数 failed_cases {int(metrics['failed_cases'])} > {max_failed_cases}"
        )
    if metrics["invalid_cases"] > max_invalid_cases:
        failures.append(
            f"无效案例数 invalid_cases {int(metrics['invalid_cases'])} > {max_invalid_cases}"
        )
    output = {
        "metrics": metrics,
        "thresholds": threshold_data,
        "cases": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "invalid": result.invalid,
                "failures": result.failures,
            }
            for result in results
        ],
        "passed": not failures,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法加载评估文件 {path}：{exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
