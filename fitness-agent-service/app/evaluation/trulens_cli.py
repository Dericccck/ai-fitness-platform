"""Run TruLens semantic evaluation on sanitized, offline case data."""

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
    validate_thresholds,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--judge", action="store_true", help="call the configured DeepSeek Judge")
    parser.add_argument("--no-persist", action="store_true", help="do not write the TruLens DB")
    args = parser.parse_args(argv)
    try:
        evaluator = TruLensEvaluator(get_settings(), persist=not args.no_persist, judge=args.judge)
        results = [evaluator.evaluate_case(case) for case in load_cases(args.cases)]
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
