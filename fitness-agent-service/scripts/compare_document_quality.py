"""比较两份同一批知识源的解析质量报告，并检测解析回归。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.rag.document_quality import compare_quality_reports


def _read_report(path: Path) -> dict[str, Any]:
    """读取并校验 JSON 根对象，具体来源一致性由领域比较器校验。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"质量报告根节点必须是对象：{path}")
    return data


def main(argv: list[str] | None = None) -> int:
    """输出差异报告；发现质量回归时返回非零退出码。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="可选：同时将差异报告写入该文件")
    args = parser.parse_args(argv)
    try:
        comparison = compare_quality_reports(
            _read_report(args.before),
            _read_report(args.after),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"quality comparison failed: {exc}", file=sys.stderr)
        return 2

    serialized = json.dumps(comparison, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 1 if comparison["regressed_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
