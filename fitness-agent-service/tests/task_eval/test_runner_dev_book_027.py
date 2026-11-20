from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import runner_dev_sandbox as sandbox
from app.evaluation.task_eval.cli import main
from app.evaluation.task_eval.dataset import load_dataset
from tests.task_eval.test_runner_dev_sandbox import observation

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
MODULES = {
    "driver_sha256": "dev_sandbox_booking.py",
    "step_kernel_sha256": "step_kernel.py",
    "booking_observer_sha256": "dev_booking_observer.py",
    "restricted_audit_sha256": "mysql_restricted_audit.py",
    "audit_observation_sha256": "audit_observation.py",
    "book_027_driver_sha256": "dev_sandbox_book_027.py",
    "book_027_fixture_loader_sha256": "booking_dev_027.py",
    "booking_model_boundary_sha256": "booking_model_boundary.py",
    "budget_store_sha256": "budget_store.py",
    "private_credential_sha256": "private_credential.py",
}
OPTIONS = {
    "credential_file": "/private/key",
    "budget_file": "/private/budget",
    "budget_limit_nano": 100,
    "budget_opening_nano": 1,
    "minimum_reserved_nano": 1,
    "confirm_current_pricing": True,
}


def sample():
    dataset = load_dataset(DATASET, check_quotas=False)
    case = next(item for item in dataset.cases if item.case_id == "BOOK-027")
    raw = observation(replace(dataset, cases=[case]))
    raw.update(
        dataset_sha256=sandbox._BOOK_DATASET_SHA256,
        fixture_sha256=sandbox._BOOK_FIXTURES[case.case_id],
        origin="synthetic_real_model_real_local_postgres_mysql_java",
        model_diagnostics={"attempts": 1, "http_status": 200},
    )
    root = Path(sandbox.__file__).parent
    raw["source_identity"].update(
        {
            key: hashlib.sha256((root / file).read_bytes()).hexdigest()
            for key, file in MODULES.items()
        }
    )
    raw["trial"]["executed_steps"] = [
        {"index": i, "type": step.type, "status": "COMPLETED"} for i, step in enumerate(case.steps)
    ]
    raw["trial"]["assertions"].extend(
        [
            {
                "id": "supplemental_pre_decision_snapshot",
                "rule": "booking_snapshot_unchanged",
                "phase": "before_decision",
                "risk": "unconfirmed_write",
                "required": True,
                "applicable": True,
                "status": "PASS",
            },
            {
                "id": "supplemental_single_effect",
                "rule": "booking_created_once",
                "phase": "final",
                "risk": "duplicate_write",
                "required": True,
                "applicable": True,
                "status": "UNKNOWN",
            },
        ]
    )
    fault = next(a for a in raw["trial"]["assertions"] if a["id"] == "fault_observed")
    fault.update(status="PASS", evidence_ref_sha256=["a" * 64])
    raw["trial"]["response_loss"] = {
        "armed": True,
        "observed": True,
        "decision_count": 1,
        "business_attempts": 1,
    }
    raw["trial"]["snapshot_checks"] = [
        {
            "phase": "before_decision",
            "status": "MATCH",
            "authorization_status": "PENDING",
            "execution_status": "NOT_STARTED",
            "deltas": {
                key: 0
                for key in (
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
                )
            },
        },
        {
            "phase": "final",
            "status": "MATCH",
            "authorization_status": "APPROVED",
            "execution_status": "UNKNOWN",
            "deltas": {
                "global_contracts": 0,
                "global_remaining_hours": -1,
                "global_appointments": 1,
                "global_create_operations": 1,
                "global_reschedule_operations": 0,
                "global_cancel_operations": 0,
                "global_audits": 1,
                "global_consumptions": 1,
                "global_outbox": 1,
                "witness_contract_count": 0,
                "witness_remaining_hours": 0,
                "witness_appointments": 0,
            },
        },
    ]
    return dataset, raw


def run(tmp_path, monkeypatch, raw, exit_code=3):
    def launch(path, *, case_id, real_model_options):
        assert case_id == "BOOK-027" and real_model_options == OPTIONS
        path.write_text(json.dumps(raw), encoding="utf-8")
        return exit_code

    monkeypatch.setattr(sandbox, "_run_launcher", launch)
    out = tmp_path / "report"
    result = main(
        [
            "run",
            "--dataset",
            str(DATASET),
            "--split",
            "dev",
            "--mode",
            "integration_sandbox",
            "--execute",
            "--dev-real-model-case",
            "BOOK-027",
            "--credential-file",
            "/private/key",
            "--budget-file",
            "/private/budget",
            "--budget-limit-nano",
            "100",
            "--budget-opening-nano",
            "1",
            "--minimum-reserved-nano",
            "1",
            "--confirm-current-pricing",
            "--output-dir",
            str(out),
        ]
    )
    return result, out


def test_real_model_case_produces_draft_report_with_bound_source(tmp_path, monkeypatch):
    _, raw = sample()
    rc, out = run(tmp_path, monkeypatch, raw)
    assert rc == 3
    manifest = json.loads((out / "manifest.json").read_text())
    trial = json.loads((out / "trials.jsonl").read_text())
    assert manifest["status"] == "INCOMPLETE" and manifest["review"] == "draft"
    assert manifest["model_call_attempted"] is True and manifest["selected_case_ids"] == [
        "BOOK-027"
    ]
    assert (
        manifest["source_identity"]["book_027_driver_sha256"]
        == raw["source_identity"]["book_027_driver_sha256"]
    )
    assert trial["status"] == "INCONCLUSIVE"
    assert trial["sandbox_observation"]["response_loss"]["business_attempts"] == 1
    assert "/private/" not in (out / "manifest.json").read_text()


@pytest.mark.parametrize(
    "kind", ["fixture", "source", "fault", "attempts", "commit", "supplement", "idem"]
)
def test_inconsistent_pass_or_binding_never_publishes(tmp_path, monkeypatch, kind):
    _, raw = sample()
    if kind == "fixture":
        raw["fixture_sha256"] = "b" * 64
    elif kind == "source":
        raw["source_identity"]["book_027_driver_sha256"] = "b" * 64
    elif kind == "fault":
        raw["trial"]["response_loss"]["observed"] = False
    elif kind == "attempts":
        raw["trial"]["response_loss"]["business_attempts"] = 2
    elif kind == "commit":
        raw["trial"]["snapshot_checks"][1]["deltas"]["global_appointments"] = 0
    elif kind == "supplement":
        raw["trial"]["assertions"][-2]["risk"] = None
    else:
        raw["trial"]["assertions"][-1]["status"] = "PASS"
    rc, out = run(tmp_path, monkeypatch, raw)
    assert rc == 1 and not (out / "manifest.json").exists()


def test_supplemental_failure_is_preserved(tmp_path, monkeypatch):
    _, raw = sample()
    raw["status"] = "FAIL"
    raw["trial"]["task_status"] = "FAIL"
    before = raw["trial"]["assertions"][-2]
    before["status"] = "FAIL"
    raw["trial"]["snapshot_checks"][0]["status"] = "MISMATCH"
    rc, out = run(tmp_path, monkeypatch, raw, 2)
    assert rc == 2
    trial = json.loads((out / "trials.jsonl").read_text())
    assert trial["status"] == "FAIL" and trial["assertions"][-2]["status"] == "FAIL"


def test_launcher_mutation_during_trial_is_rejected(tmp_path, monkeypatch):
    dataset, raw = sample()
    launcher_path = tmp_path / "launcher.sh"
    launcher_path.write_text("original")
    monkeypatch.setattr(sandbox, "_LAUNCHER", launcher_path)

    def launch(path, **kwargs):
        path.write_text(json.dumps(raw))
        launcher_path.write_text("modified")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", launch)
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset,
            split="dev",
            mode="integration_sandbox",
            repeats=1,
            seed=0,
            output_dir=str(output),
            case_id="BOOK-027",
            real_model_options=OPTIONS,
        )
    assert not (output / "manifest.json").exists()
