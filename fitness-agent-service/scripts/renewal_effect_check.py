"""对脱敏聚合续费数据计算描述性差值和置信区间；不代替真实实验。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.evidence_statistics import renewal_effect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = renewal_effect(json.loads(args.input.read_text(encoding="utf-8")))
    except (ValueError, TypeError, KeyError):
        print(json.dumps({"status": "INVALID_DATA", "renewal_uplift_pp": None}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
