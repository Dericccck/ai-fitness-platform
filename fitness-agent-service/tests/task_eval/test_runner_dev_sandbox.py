import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import runner_dev_sandbox as sandbox
from app.evaluation.task_eval.cli import main
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/dev_sandbox_v1/case.jsonl"


def observation(dataset):
    case = dataset.cases[0]
    assertions = [
        {
            "id": a.id,
            "rule": a.rule,
            "phase": a.phase,
            "required": a.required,
            "risk": a.risk,
            "applicable": a.applicable,
            "status": "UNKNOWN",
            "reason": "NOT_PROVEN",
        }
        for a in case.expected.assertions
    ]
    return {
        "status": "INCOMPLETE",
        "origin": "synthetic_scripted_model_real_local_postgres_mysql_java",
        "case_sha256": sandbox._CASE_SHA256,
        "fixture_sha256": sandbox._FIXTURE_SHA256,
        "review": "draft",
        "audit_complete": False,
        "run_id": "synthetic-test-run",
        "source_identity": {"revision": "a" * 40, "working_tree_dirty": True},
        "trial": {
            "case_id": case.case_id,
            "trial_id": "synthetic-trial",
            "task_status": "INCONCLUSIVE",
            "executed_steps": [{"index": 0, "type": "chat", "status": "COMPLETED"}],
            "assertions": assertions,
            "reason": None,
        },
    }


def test_cli_writes_explicit_scripted_incomplete_report(tmp_path, monkeypatch, capsys):
    dataset = load_dataset(DATASET, check_quotas=False)
    calls = []

    def fake_launch(path):
        calls.append(path)
        path.write_text(json.dumps(observation(dataset)), encoding="utf-8")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "report"
    rc = main(
        [
            "run",
            "--dataset",
            str(DATASET),
            "--no-quota-check",
            "--split",
            "dev",
            "--mode",
            "integration_sandbox",
            "--execute",
            "--dev-scripted-sandbox",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 3 and len(calls) == 1
    manifest = json.loads((out / "manifest.json").read_text())
    trial = json.loads((out / "trials.jsonl").read_text())
    assert manifest["review"] == "draft" and manifest["status"] == "INCOMPLETE"
    assert manifest["model"] == "scripted_test_double" and "synthetic" in manifest["result_origin"]
    assert trial["status"] == "INCONCLUSIVE" and trial["executed"] is True
    assert all(a["status"] == "UNKNOWN" for a in trial["assertions"])
    assert json.loads(capsys.readouterr().out)["status"] == "INCOMPLETE"


@pytest.mark.parametrize(
    "patch",
    [
        {"split": "test"},
        {"mode": "model_sandbox"},
        {"mode": "contract"},
        {"repeats": 2},
        {"seed": 1},
    ],
)
def test_invalid_scope_never_launches(tmp_path, monkeypatch, patch):
    dataset = load_dataset(DATASET, check_quotas=False)
    monkeypatch.setattr(sandbox, "_run_launcher", lambda *_args: pytest.fail("launched"))
    opts = {
        "split": "dev",
        "mode": "integration_sandbox",
        "repeats": 1,
        "seed": 0,
        "output_dir": str(tmp_path / "out"),
    }
    with pytest.raises(ValueError, match="unsupported"):
        sandbox.execute_development_sandbox(dataset, **(opts | patch))
    assert not (tmp_path / "out").exists()


def test_forged_dataset_object_and_existing_output_never_launch(tmp_path, monkeypatch):
    dataset = load_dataset(DATASET, check_quotas=False)
    monkeypatch.setattr(sandbox, "_run_launcher", lambda *_args: pytest.fail("launched"))
    case = dataset.cases[0]
    altered = case.model_copy(
        update={"steps": [case.steps[0].model_copy(update={"message": "changed"}), *case.steps[1:]]}
    )
    forged = replace(dataset, cases=[altered])
    options = {
        "split": "dev",
        "mode": "integration_sandbox",
        "repeats": 1,
        "seed": 0,
        "output_dir": str(tmp_path / "out"),
    }
    with pytest.raises(ValueError, match="case contract mismatch"):
        sandbox.execute_development_sandbox(forged, **options)
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        sandbox.execute_development_sandbox(dataset, **options)


def test_invalid_or_missing_launcher_report_never_publishes_manifest(tmp_path, monkeypatch):
    dataset = load_dataset(DATASET, check_quotas=False)

    def launch(path):
        path.write_text('{"status":"PASS"}')
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", launch)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset, split="dev", mode="integration_sandbox", repeats=1, seed=0, output_dir=str(out)
        )
    assert not (out / "manifest.json").exists()


def test_cli_retains_required_failure_and_exits_two(tmp_path, monkeypatch, capsys):
    dataset = load_dataset(DATASET, check_quotas=False)

    def fake_launch(path):
        value = observation(dataset)
        value["status"] = "FAIL"
        value["trial"]["task_status"] = "FAIL"
        value["trial"]["assertions"][2]["status"] = "FAIL"
        value["trial"]["assertions"][2]["evidence_ref_sha256"] = ["a" * 64]
        value["trial"]["executed_steps"].append(
            {"index": 1, "type": "assert", "status": "COMPLETED"}
        )
        path.write_text(json.dumps(value), encoding="utf-8")
        return 2

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "report"
    rc = main(
        [
            "run",
            "--dataset",
            str(DATASET),
            "--no-quota-check",
            "--split",
            "dev",
            "--mode",
            "integration_sandbox",
            "--execute",
            "--dev-scripted-sandbox",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 2
    assert json.loads((out / "manifest.json").read_text())["status"] == "FAIL"
    assert json.loads((out / "trials.jsonl").read_text())["status"] == "FAIL"
    assert json.loads(capsys.readouterr().out)["status"] == "FAIL"


def test_failure_status_without_failed_required_assertion_is_not_accepted(tmp_path, monkeypatch):
    dataset = load_dataset(DATASET, check_quotas=False)

    def fake_launch(path):
        value = observation(dataset)
        value["status"] = "FAIL"
        value["trial"]["task_status"] = "FAIL"
        path.write_text(json.dumps(value), encoding="utf-8")
        return 2

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset, split="dev", mode="integration_sandbox", repeats=1, seed=0, output_dir=str(out)
        )
    assert not (out / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["missing_fail_evidence", "coerced_required"])
def test_incomplete_or_malformed_failure_cannot_become_verified(tmp_path, monkeypatch, kind):
    dataset = load_dataset(DATASET, check_quotas=False)

    def fake_launch(path):
        value = observation(dataset)
        value["trial"]["assertions"][2]["status"] = "FAIL"
        if kind == "coerced_required":
            value["trial"]["assertions"][2]["required"] = 1
            value["trial"]["assertions"][2]["evidence_ref_sha256"] = ["a" * 64]
        path.write_text(json.dumps(value), encoding="utf-8")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset, split="dev", mode="integration_sandbox", repeats=1, seed=0, output_dir=str(out)
        )
    assert not (out / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["unexpected_step", "pass_without_reference", "invalid_source"])
def test_invalid_step_or_observation_contract_is_rejected(tmp_path, monkeypatch, kind):
    dataset = load_dataset(DATASET, check_quotas=False)

    def fake_launch(path):
        value = observation(dataset)
        if kind == "unexpected_step":
            value["trial"]["executed_steps"][0]["type"] = "decision"
        elif kind == "pass_without_reference":
            value["trial"]["assertions"][0]["status"] = "PASS"
        else:
            value["source_identity"] = None
        path.write_text(json.dumps(value), encoding="utf-8")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset, split="dev", mode="integration_sandbox", repeats=1, seed=0, output_dir=str(out)
        )
    assert not (out / "manifest.json").exists()


def test_unobserved_final_pass_cannot_be_published(tmp_path, monkeypatch):
    dataset = load_dataset(DATASET, check_quotas=False)

    def fake_launch(path):
        value = observation(dataset)
        final = value["trial"]["assertions"][0]
        final["status"] = "PASS"
        final["evidence_ref_sha256"] = ["a" * 64]
        path.write_text(json.dumps(value), encoding="utf-8")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", fake_launch)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="not verified"):
        sandbox.execute_development_sandbox(
            dataset, split="dev", mode="integration_sandbox", repeats=1, seed=0, output_dir=str(out)
        )
    assert not (out / "manifest.json").exists()


def test_book_018_cli_runs_only_explicit_case(tmp_path, monkeypatch, capsys):
    source = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    dataset = load_dataset(source, check_quotas=False)
    case = next(case for case in dataset.cases if case.case_id == "BOOK-018")
    fake_dataset = replace(dataset, cases=[case])
    captured = []

    def launch(path, *, case_id):
        captured.append(case_id)
        raw = observation(fake_dataset)
        raw.pop("case_sha256")
        raw["dataset_sha256"] = sandbox._BOOK_DATASET_SHA256
        raw["fixture_sha256"] = sandbox._BOOK_FIXTURE_SHA256
        path.write_text(json.dumps(raw), encoding="utf-8")
        return 3

    monkeypatch.setattr(sandbox, "_run_launcher", launch)
    out = tmp_path / "report"
    rc = main(
        [
            "run",
            "--dataset",
            str(source),
            "--split",
            "dev",
            "--mode",
            "integration_sandbox",
            "--execute",
            "--dev-scripted-sandbox",
            "--dev-scripted-case",
            "BOOK-018",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 3 and captured == ["BOOK-018"]
    manifest = json.loads((out / "manifest.json").read_text())
    trial = json.loads((out / "trials.jsonl").read_text())
    assert manifest["selected_case_ids"] == ["BOOK-018"]
    assert manifest["dataset_sha256"] == sandbox._BOOK_DATASET_SHA256
    assert manifest["fixture_sha256"] == sandbox._BOOK_FIXTURE_SHA256
    assert manifest["selection"]["scope"] == "single_case"
    assert manifest["review"] == "draft" and manifest["model"] == "scripted_test_double"
    assert trial["status"] == "INCONCLUSIVE" and trial["case_id"] == "BOOK-018"
    assert json.loads(capsys.readouterr().out)["status"] == "INCOMPLETE"


def test_book_018_rejects_altered_other_cases_and_unselected_case(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    dataset = load_dataset(source, check_quotas=False)
    altered = dataset.cases[0].model_copy(update={"family_id": "changed"})
    forged = replace(dataset, cases=[altered, *dataset.cases[1:]])
    monkeypatch.setattr(sandbox, "_run_launcher", lambda *args, **kw: pytest.fail("launched"))
    options = {
        "split": "dev",
        "mode": "integration_sandbox",
        "repeats": 1,
        "seed": 0,
        "output_dir": str(tmp_path / "out"),
        "case_id": "BOOK-018",
    }
    with pytest.raises(ValueError, match="case contract mismatch"):
        sandbox.execute_development_sandbox(forged, **options)
    with pytest.raises(ValueError, match="unsupported"):
        sandbox.execute_development_sandbox(dataset, **(options | {"case_id": "BOOK-019"}))
    assert not (tmp_path / "out").exists()
