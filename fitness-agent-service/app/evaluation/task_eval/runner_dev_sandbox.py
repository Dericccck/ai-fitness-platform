"""Explicit fixed-case development sandbox entry, never release evidence.

The launcher creates and owns all local containers and services. Only fixed
synthetic cases are supported; real model calls additionally require an
existing private credential file, budget ledger and explicit price check.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dataset import Dataset, load_dataset
from .report import markdown, summarize

_CASE_SHA256 = "4acffd6a435ded4f82fe50bd04b5dd0af6e8265dadd15b6196a8c968f16412e1"
_FIXTURE_SHA256 = "583f13a0aeb889a2941bfbdb60bc7ea70606920a5698d8e143ea2ba75e986170"
_CASE_ID = "DEV-SANDBOX-BOOK-001"
_LAUNCHER = Path(__file__).resolve().parents[3] / "scripts/task_eval_cross_service_booking.sh"
_DATASET = Path(__file__).resolve().parents[3] / "evals/task_eval/dev_sandbox_v1/case.jsonl"
_BOOK_DATASET = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
_BOOK_DATASET_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_BOOK_FIXTURES = {
    "BOOK-018": "2f2ddb98054ba5fd5af4fd8f2771b5b1bbdf9c14086b022bde7fdb03137d3652",
    "BOOK-026": "526729dd5ec9b79a0858189612448c3a5066b5271545da7cae8112b7f6c0a784",
    "BOOK-027": "05f0f6e6f004a04a5571d9307e77ec50d16cae2dd1e051e59f53f99b9b624134",
}
_BOOK_FIXTURE_SHA256 = _BOOK_FIXTURES["BOOK-018"]


def _run_launcher(
    report_file: Path,
    *,
    case_id: str = _CASE_ID,
    timeout_seconds: float = 420,
    real_model_options: dict[str, Any] | None = None,
) -> int:
    """Launch only the checked-in disposable harness; request cleanup on interruption."""
    flags = {
        _CASE_ID: "--single-development-trial",
        "BOOK-018": "--book-018-development-trial",
        "BOOK-026": "--book-026-development-trial",
        "BOOK-027": "--book-027-development-trial",
    }
    if case_id not in flags:
        raise ValueError("unsupported development case")
    args = ["bash", str(_LAUNCHER), flags[case_id], str(report_file)]
    if real_model_options is not None:
        if case_id not in _BOOK_FIXTURES:
            raise ValueError("unsupported real model case")
        args[2] = f"--{case_id.lower()}-real-model-trial"
        args.extend(
            [
                "--credential-file",
                str(real_model_options["credential_file"]),
                "--budget-file",
                str(real_model_options["budget_file"]),
                "--budget-limit-nano",
                str(real_model_options["budget_limit_nano"]),
                "--budget-opening-nano",
                str(real_model_options["budget_opening_nano"]),
                "--minimum-reserved-nano",
                str(real_model_options["minimum_reserved_nano"]),
                "--confirm-current-pricing",
            ]
        )
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return proc.wait(timeout=timeout_seconds)
    except BaseException:
        # Signal the entire fresh process group. The shell's EXIT/TERM trap owns
        # cleanup; a forced kill is only a last resort and cannot prove cleanup.
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        raise


def _publish(
    directory: Path, manifest: dict[str, Any], trial: dict[str, Any], summary: dict[str, Any]
) -> None:
    """Publish each new file without replacement; the manifest is the final marker."""
    contents = {
        "trials.jsonl": json.dumps(trial, ensure_ascii=False) + "\n",
        "summary.json": json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "report.md": markdown(manifest, summary),
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }
    with tempfile.TemporaryDirectory(prefix=".task-eval-stage-", dir=directory) as staging:
        for name, value in contents.items():
            staged = Path(staging) / name
            staged.write_text(value, encoding="utf-8")
            # Hard-link creates the destination atomically and fails if it exists.
            # Publishing the manifest last means incomplete output is not readable
            # as a completed report by the standard reader.
            os.link(staged, directory / name)


def _validate_book_027_observation(local: dict[str, Any], rows: dict[str, Any]) -> None:
    """Require case-specific observations for every claimed passing result."""
    snapshots = local.get("snapshot_checks")
    if not isinstance(snapshots, list):
        if any(
            rows[key].get("status") == "PASS"
            for key in (
                "fault_observed",
                "supplemental_pre_decision_snapshot",
                "supplemental_single_effect",
            )
        ):
            raise ValueError("BOOK-027 passing assertion lacks snapshots")
        return

    def phase(name: str) -> dict[str, Any]:
        matching = [row for row in snapshots if isinstance(row, dict) and row.get("phase") == name]
        if len(matching) != 1:
            raise ValueError("BOOK-027 snapshot phase missing")
        return matching[0]

    delta_keys = {
        "global_contracts",
        "global_remaining_hours",
        "global_appointments",
        "global_create_operations",
        "global_reschedule_operations",
        "global_cancel_operations",
        "global_audits",
        "global_consumptions",
        "global_outbox",
        "witness_contract_count",
        "witness_remaining_hours",
        "witness_appointments",
    }
    for key, phase_name in (
        ("supplemental_pre_decision_snapshot", "before_decision"),
        ("supplemental_single_effect", "final"),
    ):
        if rows[key].get("status") == "FAIL" and phase(phase_name).get("status") != "MISMATCH":
            raise ValueError("BOOK-027 supplemental failure lacks matching observation")
    if rows["supplemental_pre_decision_snapshot"].get("status") == "PASS":
        before = phase("before_decision")
        deltas = before.get("deltas")
        if (
            before.get("status") != "MATCH"
            or before.get("authorization_status") != "PENDING"
            or before.get("execution_status") != "NOT_STARTED"
            or not isinstance(deltas, dict)
            or set(deltas) != delta_keys
            or any(type(value) is not int or value != 0 for value in deltas.values())
        ):
            raise ValueError("BOOK-027 pre-decision snapshot mismatch")
    if rows["supplemental_single_effect"].get("status") == "PASS":
        # This driver never performs an idempotent retry and cannot prove this rule.
        raise ValueError("BOOK-027 idempotency is not proven")
    if rows["fault_observed"].get("status") == "PASS":
        final = phase("final")
        deltas = final.get("deltas")
        expected = {key: 0 for key in delta_keys}
        expected.update(
            {
                "global_remaining_hours": -1,
                "global_appointments": 1,
                "global_create_operations": 1,
                "global_audits": 1,
                "global_consumptions": 1,
                "global_outbox": 1,
            }
        )
        loss = local.get("response_loss")
        if (
            not isinstance(loss, dict)
            or loss.get("armed") is not True
            or loss.get("observed") is not True
            or type(loss.get("decision_count")) is not int
            or loss["decision_count"] != 1
            or type(loss.get("business_attempts")) is not int
            or loss["business_attempts"] != 1
            or final.get("status") != "MATCH"
            or final.get("authorization_status") != "APPROVED"
            or final.get("execution_status") != "UNKNOWN"
            or not isinstance(deltas, dict)
            or set(deltas) != delta_keys
            or any(
                type(deltas.get(key)) is not int or deltas[key] != value
                for key, value in expected.items()
            )
        ):
            raise ValueError("BOOK-027 response-loss observation mismatch")


def execute_development_sandbox(
    dataset: Dataset,
    *,
    split: str,
    mode: str,
    repeats: int,
    seed: int,
    output_dir: str,
    case_id: str = _CASE_ID,
    real_model_options: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Run one fixed unreviewed development trial through isolated local services."""
    if case_id == _CASE_ID:
        dataset_path, dataset_sha, fixture_sha, expected_version = (
            _DATASET,
            _CASE_SHA256,
            _FIXTURE_SHA256,
            "dev-sandbox-booking-v1",
        )
    elif case_id in _BOOK_FIXTURES:
        dataset_path, dataset_sha, fixture_sha, expected_version = (
            _BOOK_DATASET,
            _BOOK_DATASET_SHA256,
            _BOOK_FIXTURES[case_id],
            "fitness-task-planning-v1",
        )
    else:
        raise ValueError("unsupported development sandbox selection")
    if real_model_options is not None and (
        case_id not in _BOOK_FIXTURES
        or set(real_model_options)
        != {
            "credential_file",
            "budget_file",
            "budget_limit_nano",
            "budget_opening_nano",
            "minimum_reserved_nano",
            "confirm_current_pricing",
        }
        or real_model_options["confirm_current_pricing"] is not True
        or any(
            not isinstance(real_model_options[key], str)
            or not Path(real_model_options[key]).is_absolute()
            for key in ("credential_file", "budget_file")
        )
        or any(
            type(real_model_options[key]) is not int or real_model_options[key] < 0
            for key in ("budget_limit_nano", "budget_opening_nano", "minimum_reserved_nano")
        )
    ):
        raise ValueError("invalid real model development sandbox configuration")
    cases = [item for item in dataset.cases if item.case_id == case_id]
    if (
        dataset.sha256 != dataset_sha
        or dataset.dataset_version != expected_version
        or len(cases) != 1
        or cases[0].review.status != "draft"
        or cases[0].capability_status != "planned"
        or cases[0].split != "dev"
        or split != "dev"
        or mode != "integration_sandbox"
        or repeats != 1
        or seed != 0
    ):
        raise ValueError("unsupported development sandbox selection")
    case = cases[0]
    frozen = load_dataset(dataset_path, check_quotas=False)
    if frozen.sha256 != dataset_sha or [item.model_dump(mode="json") for item in dataset.cases] != [
        item.model_dump(mode="json") for item in frozen.cases
    ]:
        raise ValueError("development case contract mismatch")
    source_paths = {
        "runner_adapter_sha256": Path(__file__),
        "runner_sha256": Path(__file__).with_name("runner.py"),
        "cli_sha256": Path(__file__).with_name("cli.py"),
    }
    observed_source_paths: dict[str, Path] = {}
    if case_id == "BOOK-027":
        directory = Path(__file__).parent
        observed_source_paths = {
            "driver_sha256": directory / "dev_sandbox_booking.py",
            "step_kernel_sha256": directory / "step_kernel.py",
            "booking_observer_sha256": directory / "dev_booking_observer.py",
            "restricted_audit_sha256": directory / "mysql_restricted_audit.py",
            "audit_observation_sha256": directory / "audit_observation.py",
            "book_027_driver_sha256": directory / "dev_sandbox_book_027.py",
            "book_027_fixture_loader_sha256": directory / "booking_dev_027.py",
        }
        if real_model_options is not None:
            for name in ("booking_model_boundary", "budget_store", "private_credential"):
                observed_source_paths[f"{name}_sha256"] = directory / f"{name}.py"
        source_paths["launcher_sha256"] = _LAUNCHER
        source_paths["book_027_fixture_sha256"] = (
            directory.parents[2] / "evals/task_eval/dev_fixtures/book-027-v1.json"
        )
    source_hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in source_paths.items()
    }
    expected_observed_sources = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in observed_source_paths.items()
    }
    if case_id == "BOOK-027" and source_hashes["book_027_fixture_sha256"] != fixture_sha:
        raise ValueError("development fixture changed")
    output = Path(output_dir)
    output.mkdir(parents=False, exist_ok=False)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fitness-dev-sandbox-run-") as tmp:
        sandbox_report = Path(tmp) / "observation.json"
        try:
            exit_status = (
                _run_launcher(sandbox_report)
                if case_id == _CASE_ID
                else _run_launcher(
                    sandbox_report, case_id=case_id, real_model_options=real_model_options
                )
                if real_model_options is not None
                else _run_launcher(sandbox_report, case_id=case_id)
            )
            if (
                exit_status not in {2, 3}
                or not sandbox_report.is_file()
                or sandbox_report.is_symlink()
            ):
                raise ValueError("isolated launcher failed or cleanup was not confirmed")
            if sandbox_report.stat().st_size > 1_000_000:
                raise ValueError("invalid isolated sandbox observation")
            observed = json.loads(sandbox_report.read_text(encoding="utf-8"))
            if (
                not isinstance(observed, dict)
                or observed.get("status") not in {"INCOMPLETE", "FAIL"}
                or observed.get("origin")
                != (
                    "synthetic_real_model_real_local_postgres_mysql_java"
                    if real_model_options is not None
                    else "synthetic_scripted_model_real_local_postgres_mysql_java"
                )
                or observed.get("case_sha256" if case_id == _CASE_ID else "dataset_sha256")
                != dataset_sha
                or observed.get("fixture_sha256") != fixture_sha
                or observed.get("review") != "draft"
                or observed.get("audit_complete") is not False
                or not isinstance(observed.get("trial"), dict)
                or observed["trial"].get("case_id") != case_id
                or observed["trial"].get("task_status") not in {"INCONCLUSIVE", "FAIL"}
                or not isinstance(observed.get("source_identity"), dict)
            ):
                raise ValueError("invalid isolated sandbox observation")
            if any(
                observed["source_identity"].get(name) != digest
                for name, digest in expected_observed_sources.items()
            ):
                raise ValueError("isolated sandbox source mismatch")
            model_attempted = False
            model_http_status: int | None = None
            if real_model_options is not None:
                diagnostics = observed.get("model_diagnostics")
                if (
                    not isinstance(diagnostics, dict)
                    or type(diagnostics.get("attempts")) is not int
                    or diagnostics["attempts"] not in (0, 1)
                ):
                    raise ValueError("missing real model diagnostics")
                model_attempted = diagnostics["attempts"] == 1
                http_status = diagnostics.get("http_status")
                if type(http_status) is int and 100 <= http_status <= 599:
                    model_http_status = http_status
            local = observed["trial"]
            expected_ids = {a.id for a in case.expected.assertions}
            assertions = local.get("assertions")
            supplemental_contracts = (
                {
                    "supplemental_pre_decision_snapshot": (
                        "booking_snapshot_unchanged",
                        "before_decision",
                        "unconfirmed_write",
                    ),
                    "supplemental_single_effect": (
                        "booking_created_once",
                        "final",
                        "duplicate_write",
                    ),
                }
                if case_id in {"BOOK-026", "BOOK-027"}
                else {}
            )
            if (
                not isinstance(assertions, list)
                or len(assertions) != len(expected_ids) + len(supplemental_contracts)
                or any(not isinstance(a, dict) for a in assertions)
            ):
                raise ValueError("invalid isolated sandbox assertions")
            rows = {a.get("id"): a for a in assertions if isinstance(a.get("id"), str)}
            if set(rows) != expected_ids | set(supplemental_contracts):
                raise ValueError("isolated sandbox assertion mismatch")
            for key, (rule, phase, risk) in supplemental_contracts.items():
                row = rows[key]
                if (
                    row.get("rule") != rule
                    or row.get("phase") != phase
                    or row.get("risk") != risk
                    or row.get("required") is not True
                    or row.get("applicable") is not True
                    or row.get("status") not in {"PASS", "FAIL", "UNKNOWN"}
                ):
                    raise ValueError("invalid supplemental sandbox assertion")
                if row["status"] in {"PASS", "FAIL"}:
                    snapshots = local.get("snapshot_checks")
                    if not isinstance(snapshots, list):
                        raise ValueError("supplemental snapshot missing")
                    matching = [
                        s for s in snapshots if isinstance(s, dict) and s.get("phase") == phase
                    ]
                    if len(matching) != 1:
                        raise ValueError("supplemental snapshot missing")
                    encoded = json.dumps(
                        matching[0], sort_keys=True, separators=(",", ":")
                    ).encode()
                    row["evidence_ref_sha256"] = [hashlib.sha256(encoded).hexdigest()]
                    row["evidence_origin"] = "local_snapshot_check_digest_not_complete_audit"
            if case_id == "BOOK-027":
                _validate_book_027_observation(local, rows)
            for a in case.expected.assertions:
                row = rows[a.id]
                if (
                    any(type(row.get(k)) is not bool for k in ("required", "applicable"))
                    or any(
                        row.get(k) != getattr(a, k)
                        for k in ("id", "rule", "phase", "required", "risk", "applicable")
                    )
                    or row.get("status")
                    not in {
                        "PASS",
                        "FAIL",
                        "UNKNOWN",
                        "ERROR",
                        "SKIPPED",
                        "NOT_RUN",
                        "NA",
                    }
                ):
                    raise ValueError("isolated sandbox assertion contract mismatch")
                if (a.applicable and row["status"] == "NA") or (
                    not a.applicable and row["status"] != "NA"
                ):
                    raise ValueError("isolated sandbox assertion applicability mismatch")
                if row["status"] in {"PASS", "FAIL"} and (a.required or a.risk):
                    refs = row.get("evidence_ref_sha256")
                    if (
                        not isinstance(refs, list)
                        or not refs
                        or any(
                            not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref)
                            for ref in refs
                        )
                    ):
                        raise ValueError("isolated sandbox failure lacks evidence reference")
            required_failure = any(
                row.get("status") == "FAIL" and (row.get("required") is True or row.get("risk"))
                for row in assertions
            )
            if (
                exit_status == 2
                or observed["trial"].get("task_status") == "FAIL"
                or observed.get("status") == "FAIL"
            ) and not required_failure:
                raise ValueError("isolated sandbox failure lacks required evidence")
            trial_status = "FAIL" if required_failure else "INCONCLUSIVE"
            steps = local.get("executed_steps")
            if not isinstance(steps, list) or len(steps) > len(case.steps):
                raise TypeError("invalid isolated sandbox steps")
            if any(
                not isinstance(step, dict)
                or type(step.get("index")) is not int
                or step.get("index") != index
                or step.get("type") != case.steps[index].type
                or step.get("status") != "COMPLETED"
                for index, step in enumerate(steps)
            ) or (required_failure and not steps):
                raise ValueError("isolated sandbox step sequence mismatch")
            completed_phases = {
                case.steps[index].phase
                for index in range(len(steps))
                if case.steps[index].type == "assert"
            }
            if any(
                row["status"] in {"PASS", "FAIL"} and row["phase"] not in completed_phases
                for row in assertions
            ):
                raise ValueError("isolated sandbox assertion phase was not observed")
            if any(
                hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[name]
                for name, path in source_paths.items()
            ) or any(
                hashlib.sha256(path.read_bytes()).hexdigest() != expected_observed_sources[name]
                for name, path in observed_source_paths.items()
            ):
                raise ValueError("runner source changed during sandbox execution")
            trial = {
                "trial_id": local.get("trial_id"),
                "case_id": case.case_id,
                "family_id": case.family_id,
                "split": case.split,
                "primary_scenario": case.primary_scenario,
                "delivery_scope": case.delivery_scope,
                "capability_status": case.capability_status,
                "trial_index": 0,
                "seed": seed,
                "executed": bool(steps),
                "status": trial_status,
                "reason": local.get("reason"),
                "assertions": assertions,
                "duration_ms": round((time.monotonic() - start) * 1000, 3),
                "phase_observations": local.get("phase_observations", []),
                "sandbox_observation": {
                    "executed_steps": steps,
                    "snapshot_checks": local.get("snapshot_checks", []),
                    "repeated_decision": local.get("repeated_decision")
                    or local.get("duplicate_decision"),
                    "audit_observations": local.get("audit_observations", []),
                    "response_loss": local.get("response_loss"),
                    "audit_initial_integrity": observed.get("audit_initial_integrity"),
                    "audit_final_integrity": observed.get("audit_final_integrity"),
                },
            }
            manifest = {
                "schema_version": "1.0",
                "run_id": observed.get("run_id") or uuid4().hex,
                "mode": "integration_sandbox",
                "dataset_version": dataset.dataset_version,
                "dataset_sha256": dataset.sha256,
                "revision": observed.get("source_identity", {}).get("revision", "unknown"),
                "source_identity": observed["source_identity"] | source_hashes,
                "selection": {
                    "split": "dev",
                    "scope": "all" if case_id == _CASE_ID else "single_case",
                    "include_planned": True,
                },
                "selected_case_ids": [case.case_id],
                "fixture_sha256": fixture_sha,
                "grader_version": "task-eval-rules-v1",
                "repeats": 1,
                "seed": seed,
                "adapter": "disposable_local_real_model"
                if real_model_options is not None
                else "disposable_local_scripted_model",
                "result_origin": "synthetic_real_model_local_services"
                if real_model_options is not None
                else "synthetic_scripted_model_local_services",
                "model": "deepseek-flash"
                if real_model_options is not None
                else "scripted_test_double",
                "model_call_attempted": model_attempted,
                "model_http_status": model_http_status,
                "prompt_version": "unknown",
                "tool_schema_version": "unknown",
                "index_version": "unknown",
                "environment": "disposable_local_services",
                "status": "FAIL" if required_failure else "INCOMPLETE",
                "review": "draft",
                "limitations": observed.get("limitations", []),
            }
            summary = summarize([trial], 1)
            _publish(output, manifest, trial, summary)
            return manifest, [trial], summary
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise ValueError("development sandbox failed; cleanup or report not verified") from exc
