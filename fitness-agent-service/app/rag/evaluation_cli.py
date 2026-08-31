"""确定性 RAG 检索回归用例的命令行质量门禁。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .evaluation import (
    RetrievalEvalThresholds,
    aggregate_results,
    case_from_mapping,
    evaluate_case,
)


def main(argv: list[str] | None = None) -> int:
    """评测仓库中的基准结果，并返回兼容 CI 的退出码。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args(argv)

    cases = _load_json(args.cases)
    threshold_data = _load_json(args.thresholds)
    k = int(threshold_data["k"])
    thresholds = RetrievalEvalThresholds(
        min_recall_at_k=float(threshold_data["min_recall_at_k"]),
        min_mrr=float(threshold_data["min_mrr"]),
        max_forbidden_hits=int(threshold_data.get("max_forbidden_hits", 0)),
    )
    eval_cases = [case_from_mapping(item) for item in cases]
    results = [evaluate_case(case, case.retrieved_ids, k=k) for case in eval_cases]
    metrics = aggregate_results(results)
    failures = thresholds.validate(metrics)
    output = {
        "k": k,
        "metrics": metrics,
        "thresholds": threshold_data,
        "cases": [
            {
                "case_id": result.case_id,
                "recall_at_k": result.recall_at_k,
                "mrr": result.reciprocal_rank,
                "forbidden_hits": result.forbidden_hits,
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
