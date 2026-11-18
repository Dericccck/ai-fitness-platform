"""Validate, plan, run and compare offline task evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from .comparison import IncompatibleRuns, compare_runs
from .dataset import DatasetValidationError, load_dataset
from .runner import MODES, execute, exit_code, write_report


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan", "run", "routing"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dataset", required=True)
        cmd.add_argument(
            "--no-quota-check",
            action="store_true",
            help="validate a smaller/custom dataset without 120-case quotas",
        )
        if name == "validate":
            cmd.add_argument("--require-reviewed-test", action="store_true")
        else:
            cmd.add_argument("--split", choices=("dev", "test"), required=True)
            if name != "routing":
                cmd.add_argument("--mode", choices=MODES, required=True)
                cmd.add_argument("--repeats", type=int, default=1)
                cmd.add_argument("--seed", type=int, default=0)
        if name == "routing":
            cmd.add_argument("--output", help="optional new JSON output path")
        if name == "run":
            cmd.add_argument("--execute", action="store_true", required=True)
            cmd.add_argument("--output-dir", required=True)
    gate = sub.add_parser("release-gate", help="fail-closed offline release assessment")
    gate.add_argument("--dataset", required=True)
    gate.add_argument("--manifest", required=True)
    gate.add_argument("--trials", required=True, help="trials.jsonl")
    gate.add_argument("--policy", required=True, help="pre-frozen policy JSON")
    gate.add_argument("--no-quota-check", action="store_true")
    preflight = sub.add_parser(
        "model-preflight", help="offline, fail-closed model configuration check"
    )
    preflight.add_argument("--config", required=True, help="non-secret JSON configuration")
    smoke = sub.add_parser("model-smoke", help="one opt-in synthetic model call; no Agent tools")
    smoke.add_argument("--config", required=True, help="non-secret JSON evaluation configuration")
    smoke.add_argument("--dataset", required=True)
    smoke.add_argument("--execute", action="store_true", required=True)
    smoke.add_argument("--budget-file", required=True, help="existing persistent CNY budget ledger")
    smoke.add_argument("--budget-limit-nano", required=True, type=int)
    smoke.add_argument("--budget-opening-nano", required=True, type=int)
    smoke.add_argument(
        "--ack-provider-cap-and-isolation",
        action="store_true",
        required=True,
        help="attest that external isolation and provider-side spend cap were independently verified",
    )
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", required=True, help="baseline report directory")
    compare.add_argument("--candidate", required=True, help="candidate report directory")
    compare.add_argument("--output", required=True, help="new comparison.json path")
    compare.add_argument(
        "--vary",
        choices=("revision", "model", "prompt_version", "tool_schema_version", "index_version"),
        help="one intentionally changed comparison variable",
    )
    local = sub.add_parser("local", help="run isolated local protocol/component fixtures")
    local.add_argument(
        "--suite",
        choices=("booking", "signed_identity", "retrieval", "notification", "operations", "all"),
        default="all",
    )
    local.add_argument("--output", help="optional new JSON output path")
    exploratory = sub.add_parser(
        "dataset-local", help="execute dataset-linked local exploratory fixtures"
    )
    exploratory.add_argument("--dataset", required=True)
    exploratory.add_argument("--split", choices=("dev", "test", "all"), default="all")
    exploratory.add_argument("--no-quota-check", action="store_true")
    exploratory.add_argument("--execute", action="store_true", required=True)
    exploratory.add_argument("--output-dir", required=True)
    return parser


def _read_run(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    directory = Path(path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    trials = [
        json.loads(line)
        for line in (directory / "trials.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, trials


def _read_gate_json(path: str, *, max_bytes: int) -> Any:
    raw = Path(path).read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("input too large")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=unique_pairs)


def _read_gate_trials(path: str) -> list[Any]:
    raw = Path(path).read_bytes()
    if len(raw) > 10_000_000:
        raise ValueError("input too large")
    lines = raw.decode("utf-8").splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("blank trial line")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    return [json.loads(line, object_pairs_hook=unique_pairs) for line in lines]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "release-gate":
        # Data and validation errors can contain untrusted text or secrets.
        # This dedicated boundary deliberately reports a generic input failure.
        try:
            from .release_gate import GatePolicy, assess_release_gate

            if Path(args.dataset).stat().st_size > 10_000_000:
                raise ValueError("dataset too large")
            dataset = load_dataset(args.dataset, check_quotas=not args.no_quota_check)
            manifest = _read_gate_json(args.manifest, max_bytes=2_000_000)
            trials = _read_gate_trials(args.trials)
            policy = GatePolicy.model_validate(_read_gate_json(args.policy, max_bytes=100_000))
            result = assess_release_gate(dataset, manifest, trials, policy)
        except (ValueError, OSError, TypeError, KeyError, UnicodeError):
            print("task-eval: invalid release gate input", file=sys.stderr)
            return 1
        print(_json(result))
        return {"PASS": 0, "FAIL": 2, "BLOCKED": 3}[result["status"]]
    try:
        if args.command == "dataset-local":
            from .dataset_local import evaluate_dataset, write_dataset_report

            dataset = load_dataset(args.dataset, check_quotas=not args.no_quota_check)
            with redirect_stdout(sys.stderr):
                result = asyncio.run(evaluate_dataset(dataset, split=args.split))
            write_dataset_report(args.output_dir, result)
            print(_json(result["summary"]))
            if result["summary"]["component_failures"] or result["summary"][
                "task_status_counts"
            ].get("FAIL", 0):
                return 2
            if result["summary"]["task_status_counts"].get("ERROR", 0):
                return 1
            return 3
        if args.command == "local":
            from .local_suite import run_local_suite

            # Runtime libraries can log to stdout; keep the command's stdout valid JSON.
            with redirect_stdout(sys.stderr):
                result = asyncio.run(run_local_suite(args.suite))
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as stream:
                    stream.write(_json(result) + "\n")
            print(_json(result))
            return {"PASS": 0, "FAIL": 2, "INCOMPLETE": 3}[result["status"]]
        if args.command in {"model-preflight", "model-smoke"}:
            from pydantic import ValidationError

            from .model_safety import ModelPreflightConfig, preflight

            try:
                raw = Path(args.config).read_bytes()
                if len(raw) > 65536:
                    raise ValueError("configuration too large")
                config = ModelPreflightConfig.model_validate_json(raw)
            except (ValidationError, ValueError):
                raise ValueError(
                    "invalid model preflight configuration; no execution was attempted"
                ) from None
            credential = os.environ.get(config.credential_env, "")
            if args.command == "model-preflight":
                result = preflight(config, credential_available=bool(credential.strip()))
            else:
                from .budget_store import DurableBudget
                from .model_smoke import smoke_once

                dataset = load_dataset(args.dataset)
                budget = DurableBudget.open_existing(
                    args.budget_file,
                    limit_nano=args.budget_limit_nano,
                    opening_nano=args.budget_opening_nano,
                )
                result = smoke_once(
                    dataset,
                    config,
                    credential=credential,
                    execute=args.execute,
                    confirmed_external_controls=args.ack_provider_cap_and_isolation,
                    durable_budget=budget,
                )
            print(_json(result))
            return 3  # This is not a complete Agent release evaluation.
        if args.command == "compare":
            base_manifest, base_trials = _read_run(args.baseline)
            candidate_manifest, candidate_trials = _read_run(args.candidate)
            result = compare_runs(
                base_manifest, base_trials, candidate_manifest, candidate_trials, vary=args.vary
            )
            with Path(args.output).open("x", encoding="utf-8") as stream:
                stream.write(_json(result) + "\n")
            print(_json({"output": args.output, "counts": result["counts"]}))
            if result["counts"]["regressed"]:
                return 2
            return 3 if result["counts"]["incomparable"] else 0
        dataset = load_dataset(
            args.dataset,
            require_reviewed_test=getattr(args, "require_reviewed_test", False),
            check_quotas=not args.no_quota_check,
        )
        if args.command == "validate":
            print(
                _json(
                    {
                        "dataset_version": dataset.dataset_version,
                        "sha256": dataset.sha256,
                        "case_count": len(dataset.cases),
                        "coverage": dataset.coverage,
                        "warnings": dataset.warnings,
                    }
                )
            )
            return 0
        if args.command == "routing":
            from .component_routing import evaluate_routing

            result = evaluate_routing(dataset, split=args.split)
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as stream:
                    stream.write(_json(result) + "\n")
            print(_json(result))
            counts = result["counts"]
            if counts["FAIL"]:
                return 2
            if (
                counts["SKIPPED"]
                or counts["ERROR"]
                or not result["cases"]
                or any(c["review_status"] != "reviewed" for c in result["cases"])
            ):
                return 3
            return 0
        selected = [c for c in dataset.cases if c.split == args.split]
        if not selected or not 1 <= args.repeats <= 100:
            raise ValueError("selection empty or repeats outside 1–100")
        if args.command == "plan":
            print(
                _json(
                    {
                        "dataset_version": dataset.dataset_version,
                        "dataset_sha256": dataset.sha256,
                        "split": args.split,
                        "mode": args.mode,
                        "repeats": args.repeats,
                        "cases": len(selected),
                        "trials": len(selected) * args.repeats,
                        "in_scope": sum(c.delivery_scope == "in_scope" for c in selected),
                        "out_of_scope": sum(c.delivery_scope == "out_of_scope" for c in selected),
                        "planned": sum(c.capability_status == "planned" for c in selected),
                        "supported": sum(c.capability_status == "supported" for c in selected),
                        "adapter": "unsupported",
                        "note": "No network or Agent calls are made; real adapters are not enabled.",
                    }
                )
            )
            return 0
        manifest, trials, summary = execute(
            dataset,
            split=args.split,
            mode=args.mode,
            repeats=args.repeats,
            seed=args.seed,
            dataset_path=args.dataset,
        )
        write_report(args.output_dir, manifest, trials, summary)
        print(
            _json(
                {
                    "run_id": manifest["run_id"],
                    "status": manifest["status"],
                    "output_dir": args.output_dir,
                    "trial_counts": summary["trial_counts"],
                }
            )
        )
        return exit_code(manifest)
    except (
        DatasetValidationError,
        IncompatibleRuns,
        ValueError,
        OSError,
        sqlite3.Error,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"task-eval: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
