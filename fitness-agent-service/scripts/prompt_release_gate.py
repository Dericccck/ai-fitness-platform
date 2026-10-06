"""检查真实同集模型对比报告是否绑定本次候选版本；离线契约不是发布质量证据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evaluation.prompt_regression import SCORER_VERSION, digest, validate_dataset


def validate(
    snapshot: dict[str, Any], dataset: dict[str, Any], report: dict[str, Any], *, reviewed: bool
) -> None:
    cases = validate_dataset(dataset)
    if not reviewed:
        raise ValueError("必须完成对越权、健康风险和写入确认案例的人工复核")
    if report.get("mode") != "live_model_comparison" or report.get("passed") is not True:
        raise ValueError("必须提供通过的真实模型对比报告，不能使用离线契约或未通过报告")
    if report.get("candidate_digest") != digest(snapshot) or report.get("dataset_digest") != digest(
        dataset
    ):
        raise ValueError("评测报告与候选提示词或评测集摘要不一致")
    if report.get("model") != snapshot.get("model") or not report.get("baseline_digest"):
        raise ValueError("评测模型或基线证据缺失")
    if report.get("scorer_version") != SCORER_VERSION:
        raise ValueError("评分器版本未绑定或已过期")
    if not report.get("changed_prompt_ids"):
        raise ValueError("同一 Prompt 自我比较仅可验收管线，不能作为版本发布的 A/B 证据")
    repeats = report.get("repetitions")
    if repeats not in (1, 2, 3):
        raise ValueError("重复采样数量不符合契约")
    expected = {(case["id"], index) for case in cases for index in range(repeats)}
    for variant in ("baseline", "candidate"):
        rows = report.get("results", {}).get(variant, [])
        if (
            len(rows) != len(expected)
            or {(row.get("case_id"), row.get("repeat")) for row in rows} != expected
        ):
            raise ValueError("基线或候选报告案例未完整覆盖")
        if variant == "candidate" and any(
            row.get("failures") or "failures" not in row for row in rows
        ):
            raise ValueError("候选存在回归或评分记录缺失")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "dataset", "report"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    try:
        validate(
            *(
                json.loads(path.read_text(encoding="utf-8"))
                for path in (args.snapshot, args.dataset, args.report)
            ),
            reviewed=args.reviewed,
        )
        print(json.dumps({"passed": True, "candidate_report_verified": True}))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print(
            json.dumps(
                {"passed": False, "error": "报告绑定、真实评测或人工复核门禁未通过"},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
