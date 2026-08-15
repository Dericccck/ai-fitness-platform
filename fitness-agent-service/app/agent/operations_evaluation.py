"""Operations Agent 趋势摘要的确定性离线评测。

评测只调用本地的 ``build_operations_report``，不访问 MySQL、PostgreSQL、LLM 或线上用户
数据。它验证的是“聚合结果已经返回后，Agent 是否正确补齐时间桶和解释趋势”，不能替代
Gateway 的权限测试和真实数据库集成测试。
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

from .operations_tools import build_operations_report


@dataclass(frozen=True)
class OperationsTrendEvalCase:
    """一条带有确定性期望结果的趋势评测用例。"""

    case_id: str
    metric: str
    bucket: str
    from_date: date
    to_date: date
    rows: tuple[tuple[str, str, int], ...]
    expected_trend_available: bool
    expected_direction: str | None
    expected_series: tuple[tuple[str, int], ...]
    expected_warning_fragments: tuple[str, ...]
    expected_change_percent_null: bool = False


@dataclass(frozen=True)
class OperationsTrendEvalResult:
    """单条趋势评测的可审计结果。"""

    case_id: str
    passed: bool
    failures: tuple[str, ...]
    invalid: bool = False


@dataclass(frozen=True)
class OperationsTrendEvalThresholds:
    """Operations 趋势离线质量门槛。"""

    min_pass_rate: float
    max_failed_cases: int = 0
    max_invalid_cases: int = 0

    def validate(self, metrics: dict[str, float]) -> list[str]:
        """把质量回退转换为 CI 可识别的失败原因。"""

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


def evaluate_case(case: OperationsTrendEvalCase) -> OperationsTrendEvalResult:
    """根据真实的趋势摘要函数校验一条用例，不让模型参与判断。"""

    try:
        result = GatewayOperationsMetric.model_validate(
            {
                "metric": case.metric,
                "bucket": case.bucket,
                "organizationId": "evaluation-org",
                "from": case.from_date,
                "to": case.to_date,
                "rows": [
                    GatewayOperationsMetricRow(
                        dimension=dimension,
                        label=label,
                        value=value,
                    )
                    for dimension, label, value in case.rows
                ],
                "generatedAt": datetime(
                    case.to_date.year,
                    case.to_date.month,
                    case.to_date.day,
                    tzinfo=UTC,
                ),
            }
        )
        report = cast(dict[str, Any], build_operations_report(result))
    except (TypeError, ValueError) as exc:
        return OperationsTrendEvalResult(
            case.case_id,
            False,
            (f"invalid result: {exc}",),
            invalid=True,
        )

    failures: list[str] = []
    if report["trend_available"] != case.expected_trend_available:
        failures.append(
            f"trend_available {report['trend_available']} != {case.expected_trend_available}"
        )
    actual_series = tuple(
        (str(item["bucket"]), int(item["value"])) for item in report.get("series", [])
    )
    if actual_series != case.expected_series:
        failures.append(f"series {actual_series} != {case.expected_series}")
    if case.expected_direction is not None:
        actual_direction = report.get("trend", {}).get("direction")
        if actual_direction != case.expected_direction:
            failures.append(f"direction {actual_direction} != {case.expected_direction}")
    warnings = tuple(str(item) for item in report.get("warnings", []))
    for fragment in case.expected_warning_fragments:
        if not any(fragment in warning for warning in warnings):
            failures.append(f"missing warning fragment: {fragment}")
    if case.expected_change_percent_null:
        change_percent = report.get("trend", {}).get("change_percent")
        if change_percent is not None:
            failures.append(f"change_percent {change_percent} must be null")
    return OperationsTrendEvalResult(case.case_id, not failures, tuple(failures))


def aggregate_results(results: list[OperationsTrendEvalResult]) -> dict[str, float]:
    """汇总用例通过率和失败数量。"""

    if not results:
        return {"case_count": 0.0, "pass_rate": 0.0, "failed_cases": 0.0, "invalid_cases": 0.0}
    invalid_cases = sum(result.invalid for result in results)
    failed_cases = sum(1 for result in results if not result.passed)
    return {
        "case_count": float(len(results)),
        "pass_rate": sum(result.passed for result in results) / len(results),
        "failed_cases": float(failed_cases),
        "invalid_cases": float(invalid_cases),
    }


def case_from_mapping(data: dict[str, Any]) -> OperationsTrendEvalCase:
    """从 JSON 加载一条评测用例，并将输入收敛到固定结构。"""

    expected_series = tuple(
        (str(item["bucket"]), int(item["value"])) for item in data.get("expected_series", [])
    )
    rows = tuple(
        (str(item["dimension"]), str(item["label"]), int(item["value"]))
        for item in data.get("rows", [])
    )
    return OperationsTrendEvalCase(
        case_id=str(data["case_id"]),
        metric=str(data["metric"]),
        bucket=str(data["bucket"]),
        from_date=date.fromisoformat(str(data["from"])),
        to_date=date.fromisoformat(str(data["to"])),
        rows=rows,
        expected_trend_available=bool(data["expected_trend_available"]),
        expected_direction=(
            str(data["expected_direction"]) if data.get("expected_direction") is not None else None
        ),
        expected_series=expected_series,
        expected_warning_fragments=tuple(
            str(item) for item in data.get("expected_warning_fragments", [])
        ),
        expected_change_percent_null=bool(data.get("expected_change_percent_null", False)),
    )


def main(argv: list[str] | None = None) -> int:
    """运行 Operations 趋势离线门禁并输出机器可读 JSON。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args(argv)
    cases_data = _load_json(args.cases)
    threshold_data = _load_json(args.thresholds)
    results: list[OperationsTrendEvalResult] = []
    for item in cases_data:
        try:
            results.append(evaluate_case(case_from_mapping(item)))
        except (KeyError, TypeError, ValueError) as exc:
            case_id = str(item.get("case_id", "unknown")) if isinstance(item, dict) else "unknown"
            results.append(
                OperationsTrendEvalResult(
                    case_id,
                    False,
                    (f"invalid case: {exc}",),
                    invalid=True,
                )
            )
    metrics = aggregate_results(results)
    thresholds = OperationsTrendEvalThresholds(
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


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load evaluation file {path}: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
