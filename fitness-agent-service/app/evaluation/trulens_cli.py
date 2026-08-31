"""对已脱敏的离线案例数据运行 TruLens 语义评估。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings

from .trulens_eval import (
    TruLensEvaluationError,
    TruLensEvaluator,
    load_cases,
    load_thresholds,
    load_trace_cases,
    validate_thresholds,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", type=Path, help="离线评测案例 JSON")
    source.add_argument("--traces", type=Path, help="已脱敏的 OTLP/TruLens Trace JSON")
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--judge", action="store_true", help="调用已配置的 DeepSeek Judge")
    parser.add_argument("--no-persist", action="store_true", help="不写入 TruLens 数据库")
    args = parser.parse_args(argv)
    try:
        evaluator = TruLensEvaluator(get_settings(), persist=not args.no_persist, judge=args.judge)
        cases = load_cases(args.cases) if args.cases else load_trace_cases(args.traces)
        results = [evaluator.evaluate_case(case) for case in cases]
        thresholds = load_thresholds(args.thresholds)
        failures = validate_thresholds(results, thresholds)
    except TruLensEvaluationError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    output = {
        "passed": not failures,
        "thresholds": thresholds,
        "cases": results,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
