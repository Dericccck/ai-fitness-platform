"""Operations Agent 环比摘要的确定性离线评测。

当前生产工具只支持“当前周期 vs 上一等长周期”的受控环比；同比会继续要求澄清，
不会因为用户提到“同比”就猜测口径。本模块只验证已经进入摘要函数的两个聚合结果，
不访问数据库、LLM 或线上数据，重点保护差值、方向和除零边界。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from app.infrastructure.gateway_client import GatewayOperationsMetric, GatewayOperationsMetricRow

from .operations_tools import build_operations_comparison_report


@dataclass(frozen=True)
class OperationsComparisonEvalCase:
    """一条带有明确当前周期和上一周期期望值的环比评测用例。"""

    case_id: str
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    current_values: tuple[int, ...]
    previous_values: tuple[int, ...]
    expected_current_total: int
    expected_previous_total: int
    expected_delta: int
    expected_direction: str
    expected_change_percent: float | None
    expected_note_fragments: tuple[str, ...]


@dataclass(frozen=True)
class OperationsComparisonEvalResult:
    """单条环比评测的可审计结果。"""

    case_id: str
    passed: bool
    failures: tuple[str, ...]
    invalid: bool = False


@dataclass(frozen=True)
class OperationsComparisonEvalThresholds:
    """环比摘要质量门槛；默认要求所有黄金用例通过。"""

    min_pass_rate: float
    max_failed_cases: int = 0
    max_invalid_cases: int = 0

    def validate(self, metrics: dict[str, float]) -> list[str]:
        """把环比摘要质量回退转换成 CI 可识别的失败原因。"""

        failures: list[str] = []
        if metrics["pass_rate"] < self.min_pass_rate:
            failures.append(f"pass_rate {metrics['pass_rate']:.4f} < {self.min_pass_rate:.4f}")
        if metrics["failed_cases"] > self.max_failed_cases:
            failures.append(
                f"failed_cases {int(metrics['failed_cases'])} > {self.max_failed_cases}"
            )
        if metrics["invalid_cases"] > self.max_invalid_cases:
            failures.append(
                f"invalid_cases {int(metrics['invalid_cases'])} > {self.max_invalid_cases}"
            )
        return failures


def evaluate_case(case: OperationsComparisonEvalCase) -> OperationsComparisonEvalResult:
    """用真实环比摘要函数校验当前/上一周期的确定性结果。"""

    try:
        current = _build_metric(
            from_date=case.current_from,
            to_date=case.current_to,
            values=case.current_values,
        )
        previous = _build_metric(
            from_date=case.previous_from,
            to_date=case.previous_to,
            values=case.previous_values,
        )
        report = cast(dict[str, Any], build_operations_comparison_report(current, previous))
    except (TypeError, ValueError) as exc:
        return OperationsComparisonEvalResult(
            case.case_id,
            False,
            (f"invalid result: {exc}",),
            invalid=True,
        )

    failures: list[str] = []
    expected_periods = {
        "current_period": {
            "from": case.current_from.isoformat(),
            "to": case.current_to.isoformat(),
        },
        "previous_period": {
            "from": case.previous_from.isoformat(),
            "to": case.previous_to.isoformat(),
        },
    }
    for period_name, expected_period in expected_periods.items():
        if report.get(period_name) != expected_period:
            failures.append(f"{period_name} {report.get(period_name)} != {expected_period}")

    expected_values = {
        "current_total": case.expected_current_total,
        "previous_total": case.expected_previous_total,
        "delta": case.expected_delta,
        "direction": case.expected_direction,
        "change_percent": case.expected_change_percent,
    }
    for field, expected_value in expected_values.items():
        if report.get(field) != expected_value:
            failures.append(f"{field} {report.get(field)} != {expected_value}")

    note = str(report.get("note", ""))
    for fragment in case.expected_note_fragments:
        if fragment not in note:
            failures.append(f"missing note fragment: {fragment}")
    return OperationsComparisonEvalResult(case.case_id, not failures, tuple(failures))


def aggregate_results(results: list[OperationsComparisonEvalResult]) -> dict[str, float]:
    """汇总环比用例通过率、失败数量和输入非法数量。"""

    if not results:
        return {"case_count": 0.0, "pass_rate": 0.0, "failed_cases": 0.0, "invalid_cases": 0.0}
    return {
        "case_count": float(len(results)),
        "pass_rate": sum(result.passed for result in results) / len(results),
        "failed_cases": float(sum(not result.passed for result in results)),
        "invalid_cases": float(sum(result.invalid for result in results)),
    }


def case_from_mapping(data: dict[str, Any]) -> OperationsComparisonEvalCase:
    """从 JSON 读取用例，并转换成固定日期和聚合值结构。"""

    return OperationsComparisonEvalCase(
        case_id=str(data["case_id"]),
        current_from=date.fromisoformat(str(data["current_from"])),
        current_to=date.fromisoformat(str(data["current_to"])),
        previous_from=date.fromisoformat(str(data["previous_from"])),
        previous_to=date.fromisoformat(str(data["previous_to"])),
        current_values=tuple(int(value) for value in data.get("current_values", [])),
        previous_values=tuple(int(value) for value in data.get("previous_values", [])),
        expected_current_total=int(data["expected_current_total"]),
        expected_previous_total=int(data["expected_previous_total"]),
        expected_delta=int(data["expected_delta"]),
        expected_direction=str(data["expected_direction"]),
        expected_change_percent=(
            float(data["expected_change_percent"])
            if data.get("expected_change_percent") is not None
            else None
        ),
        expected_note_fragments=tuple(
            str(item) for item in data.get("expected_note_fragments", [])
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """运行环比摘要离线门禁并输出机器可读 JSON。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args(argv)
    cases_data = _load_json(args.cases)
    threshold_data = _load_json(args.thresholds)
    results: list[OperationsComparisonEvalResult] = []
    for item in cases_data:
        try:
            results.append(evaluate_case(case_from_mapping(item)))
        except (KeyError, TypeError, ValueError) as exc:
            case_id = str(item.get("case_id", "unknown")) if isinstance(item, dict) else "unknown"
            results.append(
                OperationsComparisonEvalResult(
                    case_id,
                    False,
                    (f"invalid case: {exc}",),
                    invalid=True,
                )
            )
    metrics = aggregate_results(results)
    thresholds = OperationsComparisonEvalThresholds(
        min_pass_rate=float(threshold_data["min_pass_rate"]),
        max_failed_cases=int(threshold_data.get("max_failed_cases", 0)),
        max_invalid_cases=int(threshold_data.get("max_invalid_cases", 0)),
    )
    failures = thresholds.validate(metrics)
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


def _build_metric(
    *, from_date: date, to_date: date, values: tuple[int, ...]
) -> GatewayOperationsMetric:
    """把黄金样例中的聚合值包装成 Gateway 真实返回模型。"""

    return GatewayOperationsMetric.model_validate(
        {
            "metric": "APPOINTMENT_COUNT",
            "organizationId": "evaluation-org",
            "from": from_date,
            "to": to_date,
            "rows": [
                GatewayOperationsMetricRow(
                    dimension=f"DIMENSION-{index}",
                    label=f"维度 {index}",
                    value=value,
                )
                for index, value in enumerate(values, start=1)
            ],
            "generatedAt": datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC),
        }
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load evaluation file {path}: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
