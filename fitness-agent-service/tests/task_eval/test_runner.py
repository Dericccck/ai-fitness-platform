import json
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval.adapters.synthetic import SyntheticAdapter
from app.evaluation.task_eval.cli import main
from app.evaluation.task_eval.contracts import TaskCase
from app.evaluation.task_eval.report import metric_summary, reduce_case, reduce_trial
from app.evaluation.task_eval.runner import execute, exit_code, run_trial


def case(**updates):
    data = {
        "schema_version": "1.0",
        "dataset_version": "v1",
        "case_id": "C1",
        "family_id": "F1",
        "split": "dev",
        "primary_scenario": "routing",
        "subcategory": "explicit_intent",
        "tags": [],
        "capability_status": "supported",
        "delivery_scope": "in_scope",
        "source": {"type": "synthetic", "reference": "test", "deidentified": True},
        "review": {
            "status": "reviewed",
            "annotator_ref": "a",
            "reviewer_ref": "b",
            "reviewed_at": "2026-01-01",
            "decision_ref": "d",
        },
        "fixture": {"id": "fake", "version": "1", "namespace_isolated": True},
        "actor": {"role": "STUDENT", "organization_ref": "org", "user_ref": "user"},
        "clock": {"mode": "real", "timezone": "UTC"},
        "trigger": {"type": "user"},
        "steps": [{"type": "chat", "message": "test"}, {"type": "assert", "phase": "final"}],
        "expected": {
            "allowed_routes": ["fitness"],
            "assertions": [
                {
                    "id": "route",
                    "phase": "final",
                    "rule": "route_allowed",
                    "required": True,
                    "parameters": {"allowed_routes": ["fitness"]},
                }
            ],
        },
        "timeouts": {"step_seconds": 1, "trial_seconds": 5, "settle_seconds": 1},
        "metadata": {"risk_level": "low", "grader_version": "1", "annotation_version": "1"},
    }
    data.update(updates)
    return TaskCase.model_validate(data)


def dataset(cases):
    return SimpleNamespace(cases=cases, sha256="abc", dataset_version="v1")


def test_synthetic_contract_only_and_no_missing_evidence_pass():
    c = case()
    adapter = SyntheticAdapter(
        {"C1": {"final": {"routes": ["fitness"], "evidence_refs": ["fixture://one"]}}}
    )
    manifest, trials, summary = execute(
        dataset([c]), split="dev", mode="contract", repeats=2, adapter=adapter
    )
    assert manifest["result_origin"] == "synthetic_contract"
    assert [t["status"] for t in trials] == ["PASS", "PASS"]
    assert summary["case_counts"] == {"PASS": 1}
    assert summary["trial_counts"] == {"PASS": 2}
    assert exit_code(manifest) == 0
    missing = SyntheticAdapter({"C1": {"final": {"routes": ["fitness"]}}})
    assert run_trial(c, 0, 0, "contract", missing)["status"] == "INCONCLUSIVE"
    assert run_trial(c, 0, 0, "contract", None)["status"] == "SKIPPED"
    with pytest.raises(ValueError):
        execute(dataset([c]), split="dev", mode="integration_sandbox", adapter=adapter)


def test_draft_planned_and_scope_do_not_disappear():
    c = case(review={"status": "draft"}, capability_status="planned")
    _, trials, summary = execute(dataset([c]), split="dev", mode="model_sandbox")
    assert trials[0]["reason"] == "CASE_NOT_REVIEWED"
    assert summary["metrics"]["route_allowed"]["planned_applicable"] == 1
    assert summary["metrics"]["route_allowed"]["counts"]["SKIPPED"] == 1
    assert summary["trial_decision_coverage"]["value"] == 0


def test_failure_precedence_and_conservative_denominators():
    assertions = [
        {"status": "FAIL", "required": False, "risk": "unconfirmed_write"},
        {"status": "UNKNOWN", "required": True, "risk": None},
    ]
    assert reduce_trial(assertions, executed=True, failure="ERROR") == "FAIL"
    assert reduce_case([{"status": "PASS"}, {"status": "SKIPPED"}], 2) == "INCONCLUSIVE"
    summary = metric_summary(["PASS", "FAIL", "UNKNOWN", "SKIPPED", "ERROR", "NOT_RUN", "NA"])
    assert summary["planned_applicable"] == 6
    assert summary["decision_coverage"]["value"] == pytest.approx(2 / 6)
    assert summary["pass_rate_decidable"]["value"] == 0.5
    assert metric_summary([])["pass_rate_decidable"]["value"] is None


def test_cli_validate_plan_and_incomplete_run(tmp_path, capsys):
    source = tmp_path / "case.jsonl"
    source.write_text(json.dumps(case().model_dump(mode="json")) + "\n", encoding="utf-8")
    args = ["--dataset", str(source), "--no-quota-check"]
    assert main(["validate", *args]) == 0
    assert main(["plan", *args, "--split", "dev", "--mode", "contract"]) == 0
    output = tmp_path / "result"
    assert (
        main(
            [
                "run",
                *args,
                "--split",
                "dev",
                "--mode",
                "contract",
                "--execute",
                "--output-dir",
                str(output),
            ]
        )
        == 3
    )
    assert json.loads((output / "manifest.json").read_text())["status"] == "INCOMPLETE"
    assert json.loads((output / "trials.jsonl").read_text())["status"] == "SKIPPED"
    assert (
        main(
            [
                "run",
                *args,
                "--split",
                "dev",
                "--mode",
                "contract",
                "--execute",
                "--output-dir",
                str(output),
            ]
        )
        == 1
    )


def test_unanswerable_recall_is_na_and_evidence_is_fingerprinted():
    expected = {
        "allowed_routes": ["fitness"],
        "assertions": [
            {
                "id": "route",
                "phase": "final",
                "rule": "route_allowed",
                "required": True,
                "parameters": {"allowed_routes": ["fitness"]},
            },
            {
                "id": "recall",
                "phase": "final",
                "rule": "required_evidence_recalled",
                "required": True,
                "parameters": {
                    "answerable": False,
                    "relevant_ids": [],
                    "forbidden_ids": [],
                    "k": 5,
                },
            },
        ],
    }
    c = case(expected=expected)
    adapter = SyntheticAdapter(
        {
            "C1": {
                "final": {
                    "routes": ["fitness"],
                    "retrieved_ids": [],
                    "evidence_refs": ["fixture://secret-ref"],
                }
            }
        }
    )
    trial = run_trial(c, 0, 0, "contract", adapter)
    assert trial["status"] == "PASS"
    assert trial["assertions"][1]["status"] == "NA"
    assert trial["assertions"][1]["metrics"]["recall_at_k"] is None
    assert trial["assertions"][0]["evidence_ref_sha256"]
    assert "fixture://secret-ref" not in json.dumps(trial)


def test_routing_cli_distinguishes_draft_incomplete_and_failure(tmp_path, monkeypatch):
    from app.evaluation.task_eval import component_routing

    source = tmp_path / "case.jsonl"
    source.write_text(json.dumps(case().model_dump(mode="json")) + "\n", encoding="utf-8")
    args = ["routing", "--dataset", str(source), "--no-quota-check", "--split", "dev"]
    result = {
        "counts": {"PASS": 1, "FAIL": 0, "SKIPPED": 0, "ERROR": 0},
        "cases": [{"review_status": "draft"}],
    }
    monkeypatch.setattr(component_routing, "evaluate_routing", lambda dataset, split: result)
    assert main(args) == 3
    result["counts"]["FAIL"] = 1
    assert main(args) == 2


@pytest.mark.parametrize(
    ("baseline_status", "candidate_status", "expected_code"),
    [("PASS", "FAIL", 2), ("PASS", "SKIPPED", 3), ("PASS", "PASS", 0)],
)
def test_compare_cli_exit_codes(tmp_path, baseline_status, candidate_status, expected_code):
    manifest = {
        "mode": "contract",
        "adapter": "stub",
        "result_origin": "synthetic",
        "seed": 0,
        "dataset_sha256": "a" * 64,
        "dataset_version": "v1",
        "selection": {"split": "dev"},
        "selected_case_ids": ["C1"],
        "fixture_sha256": "b" * 64,
        "grader_version": "v1",
        "repeats": 1,
        "revision": "rev-1",
    }
    for name, status in (("baseline", baseline_status), ("candidate", candidate_status)):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (directory / "trials.jsonl").write_text(
            json.dumps(
                {
                    "case_id": "C1",
                    "trial_index": 0,
                    "status": status,
                    "seed": 0,
                    "executed": status in {"PASS", "FAIL"},
                    "assertions": [
                        {
                            "id": "result",
                            "rule": "route_allowed",
                            "phase": "final",
                            "required": True,
                            "risk": None,
                            "applicable": True,
                            "status": status,
                            "evidence_ref_sha256": ["a" * 64],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
    assert (
        main(
            [
                "compare",
                "--baseline",
                str(tmp_path / "baseline"),
                "--candidate",
                str(tmp_path / "candidate"),
                "--output",
                str(tmp_path / "comparison.json"),
            ]
        )
        == expected_code
    )
