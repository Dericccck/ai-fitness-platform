"""对已脱敏的离线案例数据运行 TruLens 语义评估。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings

from .trulens_eval import (
    TruLensEvaluationError,
    TruLensEvaluator,
    evaluation_run_summary,
    file_fingerprint,
    load_cases,
    load_thresholds,
    load_trace_cases,
    validate_thresholds,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--cases", type=Path, help="离线评测案例 JSON")
    source_group.add_argument("--traces", type=Path, help="已脱敏的 OTLP/TruLens Trace JSON")
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--judge", action="store_true", help="调用已配置的 DeepSeek Judge")
    parser.add_argument("--no-persist", action="store_true", help="不写入 TruLens 数据库")
    parser.add_argument("--run-id", default=None, help="评测运行标识；未提供时自动生成")
    parser.add_argument(
        "--dataset-version",
        default=None,
        help="评测集版本；未提供时使用输入文件 SHA-256 摘要",
    )
    parser.add_argument("--report", type=Path, help="将完整评测报告写入指定 JSON 文件")
    args = parser.parse_args(argv)
    try:
        evaluator = TruLensEvaluator(get_settings(), persist=not args.no_persist, judge=args.judge)
        input_path = args.cases or args.traces
        assert input_path is not None
        eval_source = "cases" if args.cases else "traces"
        cases = load_cases(input_path) if args.cases else load_trace_cases(input_path)
        results = [evaluator.evaluate_case(case) for case in cases]
        thresholds = load_thresholds(args.thresholds)
        failures = validate_thresholds(results, thresholds)
    except TruLensEvaluationError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    run_id = args.run_id or f"trulens-{uuid4().hex}"
    dataset_version = args.dataset_version or file_fingerprint(input_path)
    output = {
        "passed": not failures,
        "run_id": run_id,
        "source": eval_source,
        "dataset_version": dataset_version,
        "thresholds": thresholds,
        "cases": results,
        "failures": failures,
        "summary": evaluation_run_summary(
            results,
            thresholds,
            source=eval_source,
            dataset_version=dataset_version,
            run_id=run_id,
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            print(
                json.dumps(
                    {"passed": False, "error": f"无法写入评测报告：{exc}"}, ensure_ascii=False
                )
            )
            return 2
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
