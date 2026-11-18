"""Create a hash-bound human review queue for synthetic planning cases.

The queue is an aid for reviewers. Generating it never changes review status,
certifies a reviewer, or turns a proposed case into acceptance evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .dataset import Dataset, load_dataset


def build_review_queue(dataset: Dataset) -> dict[str, Any]:
    if any(case.source.type != "synthetic" for case in dataset.cases):
        raise ValueError("review queue accepts synthetic cases only")
    records = []
    for case in dataset.cases:
        content = case.model_dump(mode="json")
        serialized = json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        records.append(
            {
                "case_id": case.case_id,
                "case_sha256": hashlib.sha256(serialized).hexdigest(),
                "case": content,
                "review_tasks": [
                    "确认输入、角色、前置条件和预期是否准确、可执行",
                    "核对断言与独立证据来源，明确不可判定的条件",
                    "检查语义族及开发集、测试集间的近似任务和共享夹具",
                    *(
                        ["由具备相应专业资质的人员审核安全风险和建议"]
                        if case.primary_scenario == "safety" or case.metadata.risk_level == "high"
                        else []
                    ),
                ],
                "review_outcome": None,
            }
        )
    return {
        "schema_version": "1.0",
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "case_count": len(records),
        "disclaimer": "仅供人工审核；未产生标注、审核结论或发布验收。",
        "cases": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    queue = build_review_queue(load_dataset(args.dataset))
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(queue, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps({"case_count": queue["case_count"], "dataset_sha256": queue["dataset_sha256"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
