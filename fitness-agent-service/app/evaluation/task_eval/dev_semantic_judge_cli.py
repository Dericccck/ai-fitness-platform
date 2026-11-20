"""Run the fixed development semantic judge; output remains unreviewed."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

from .budget_store import DurableBudget
from .dataset import load_dataset
from .dev_semantic_judge import _unique, propose_judgment
from .private_credential import read_private_credential

_MAX_INPUT = 65_536


def _private_json(path: str) -> dict[str, Any]:
    if not Path(path).is_absolute():
        raise ValueError("private input unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o177
                or not 0 < info.st_size <= _MAX_INPUT
            ):
                raise ValueError("private input unavailable")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(_MAX_INPUT + 1)
            if len(data) > _MAX_INPUT:
                raise ValueError("private input unavailable")
            value = json.loads(data, object_pairs_hook=_unique)
            if not isinstance(value, dict):
                raise TypeError("private input unavailable")
            return value
        finally:
            os.close(fd)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("private input unavailable") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--answer-file", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--budget-file", required=True)
    parser.add_argument("--budget-limit-nano", type=int, required=True)
    parser.add_argument("--budget-opening-nano", type=int, required=True)
    parser.add_argument("--minimum-reserved-nano", type=int, required=True)
    parser.add_argument("--confirm-current-pricing", action="store_true", required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if not Path(args.output).is_absolute() or not Path(args.budget_file).is_absolute():
        parser.error("output and budget paths must be absolute")
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        print(json.dumps({"status": "ERROR", "reason": "OUTPUT_UNAVAILABLE"}))
        return 1
    try:
        observation = _private_json(args.observation)
        answer_file = _private_json(args.answer_file)
        if answer_file.get("case_id") != "KNOW-020" or not isinstance(
            answer_file.get("answer"), str
        ):
            raise ValueError("private input unavailable")
        budget = DurableBudget.open_existing(
            args.budget_file,
            limit_nano=args.budget_limit_nano,
            opening_nano=args.budget_opening_nano,
        )
        snapshot = budget.snapshot()
        if (
            args.minimum_reserved_nano < 0
            or snapshot["reserved_nano"] < args.minimum_reserved_nano
            or snapshot["frozen"]
            or snapshot["pending"]
        ):
            raise ValueError("budget history unavailable")
        credential = read_private_credential(args.credential_file)
        result = asyncio.run(
            propose_judgment(
                load_dataset(args.dataset),
                "KNOW-020",
                "task_result",
                observation,
                answer_file["answer"],
                credential=credential,
                budget=budget,
                execute=True,
                confirmed_current_pricing=True,
            )
        )
        status, exit_code = result["component_status"], 3
    except Exception:  # noqa: BLE001 - provider and input errors can contain secrets
        result = {"status": "ERROR", "reason": "JUDGE_PREFLIGHT_OR_EXECUTION_FAILED"}
        status, exit_code = "ERROR", 1
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": status, "task_status": "UNKNOWN"}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
