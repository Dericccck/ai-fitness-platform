"""Gate rule tests: verifier below is a TEST DOUBLE, never a release trust adapter."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.task_eval.contracts import Review
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.release_gate import (
    GatePolicy,
    _assess,
    assess_release_gate,
    gate_policy_sha256,
)

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


class TestOnlyVerifier:
    """Untrusted stub: explicitly unsuitable for production release decisions."""

    def verify_report(self, *args):
        return True

    def verify_isolation(self, *args):
        return True

    def verify_human_review(self, *args):
        return True

    def verify_assertion(self, *args):
        return True


def setup():
    dataset = load_dataset(DATASET)
    original = next(c for c in dataset.cases if c.case_id == "SAFE-015")
    clinical_assertion = original.expected.assertions[0].model_copy(
        update={
            "parameters": {
                "metric_id": "test-clinical-safety",
                "prompt_version": "test-clinical-rubric-v1",
                "score_mapping": {"0": "FAIL", "1": "UNKNOWN", "2": "PASS"},
            }
        }
    )
    case = original.model_copy(
        update={
            "expected": original.expected.model_copy(update={"assertions": [clinical_assertion]}),
            "review": Review(
                status="reviewed",
                annotator_ref="test-only-a",
                reviewer_ref="test-only-b",
                reviewed_at="test-only-date",
                decision_ref="test-only-decision",
            ),
            "capability_status": "supported",
        }
    )
    dataset = replace(dataset, cases=[case], sha256="a" * 64)
    policy = GatePolicy(
        dataset_sha256=dataset.sha256,
        dataset_version=dataset.dataset_version,
        revision="a" * 40,
        mode="integration_sandbox",
        split="test",
        repeats=1,
        min_trial_decision_coverage=1.0,
        metric_coverage={"no_severe_unsafe_guidance": 1.0},
        risk_coverage={"unsafe_guidance": 1.0},
    )
    manifest = {
        "schema_version": "1.0",
        "gate_policy_sha256": gate_policy_sha256(policy),
        "run_id": "test-run",
        "dataset_sha256": dataset.sha256,
        "dataset_version": dataset.dataset_version,
        "revision": policy.revision,
        "mode": policy.mode,
        "repeats": 1,
        "selection": {"split": "test", "scope": "all", "include_planned": True},
        "selected_case_ids": [case.case_id],
        "fixture_sha256": "b" * 64,
        "grader_version": "v1",
        "environment": "test-only-environment",
        "model": "test-model",
        "prompt_version": "v1",
        "tool_schema_version": "v1",
        "index_version": "v1",
        "adapter": "test-only-adapter",
        "result_origin": "test-only-origin",
    }
    a = case.expected.assertions[0]
    trial = {
        "trial_id": "test-trial",
        "case_id": case.case_id,
        "trial_index": 0,
        "family_id": case.family_id,
        "split": case.split,
        "primary_scenario": case.primary_scenario,
        "delivery_scope": case.delivery_scope,
        "capability_status": case.capability_status,
        "status": "PASS",
        "executed": True,
        "assertions": [
            {
                "id": a.id,
                "rule": a.rule,
                "phase": a.phase,
                "required": a.required,
                "risk": a.risk,
                "applicable": a.applicable,
                "status": "PASS",
                "evidence_ref_sha256": ["c" * 64],
            }
        ],
    }
    return dataset, policy, manifest, [trial]


def test_rule_engine_pass_uses_only_explicit_test_double():
    dataset, policy, manifest, trials = setup()
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "PASS"
    assert result["observations"]["risk_coverage"]["unsafe_guidance"]["value"] == 1.0
    public_result = assess_release_gate(dataset, manifest, trials, policy)
    assert public_result["status"] == "BLOCKED"
    assert "ISOLATION_UNVERIFIED" in public_result["reasons"]
    assert "REPORT_PROVENANCE_UNVERIFIED" in public_result["reasons"]


def test_false_environment_and_review_claims_do_not_grant_trust():
    dataset, policy, manifest, trials = setup()
    manifest.update(
        isolated=True,
        human_review_complete=True,
        authorized=True,
        provider_cap_verified=True,
        evidence="verified",
    )
    result = assess_release_gate(dataset, manifest, trials, policy)
    assert result["status"] == "BLOCKED"
    assert any(x.startswith("HUMAN_REVIEW_UNVERIFIED") for x in result["reasons"])
    assert any(x.startswith("ASSERTION_EVIDENCE_UNVERIFIED") for x in result["reasons"])


def test_risk_failure_cannot_be_overridden_by_later_runner_error():
    dataset, policy, manifest, trials = setup()
    trials[0]["status"] = "INCONCLUSIVE"
    trials[0]["assertions"][0]["status"] = "FAIL"
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "FAIL"
    assert any(x.startswith("REQUIRED_ASSERTION_FAILED") for x in result["failures"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "unknown",
        "missing_refs",
        "false_pass",
        "wrong_review",
        "planned",
        "stub",
        "bad_revision",
        "bad_dataset",
        "bad_mode",
        "bad_split",
        "bad_scope",
        "coerced_scope",
        "bad_repeat",
        "duplicate",
        "bad_policy",
        "bad_assertion",
        "unhashable",
    ],
)
def test_invalid_incomplete_or_forged_report_cannot_pass(mutation):
    dataset, policy, manifest, trials = setup()
    if mutation == "missing":
        trials = []
    if mutation == "unknown":
        trials[0]["assertions"][0]["status"] = "UNKNOWN"
    if mutation == "missing_refs":
        trials[0]["assertions"][0].pop("evidence_ref_sha256")
    if mutation == "false_pass":
        trials[0]["executed"] = False
    if mutation == "wrong_review":
        dataset = replace(
            dataset, cases=[dataset.cases[0].model_copy(update={"review": Review(status="draft")})]
        )
    if mutation == "planned":
        dataset = replace(
            dataset, cases=[dataset.cases[0].model_copy(update={"capability_status": "planned"})]
        )
    if mutation == "stub":
        manifest["result_origin"] = "synthetic_contract"
    if mutation == "bad_revision":
        manifest["revision"] = "b" * 40
    if mutation == "bad_dataset":
        manifest["dataset_sha256"] = "b" * 64
    if mutation == "bad_mode":
        manifest["mode"] = "contract"
    if mutation == "bad_split":
        manifest["selection"]["split"] = "dev"
    if mutation == "bad_scope":
        manifest["selection"]["include_planned"] = False
    if mutation == "coerced_scope":
        manifest["selection"]["include_planned"] = 1
    if mutation == "bad_repeat":
        manifest["repeats"] = True
    if mutation == "duplicate":
        trials.append(deepcopy(trials[0]))
    if mutation == "bad_policy":
        manifest["gate_policy_sha256"] = "d" * 64
    if mutation == "bad_assertion":
        trials[0]["assertions"][0]["risk"] = None
    if mutation == "unhashable":
        trials[0]["assertions"][0]["status"] = []
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED", (mutation, result)


def test_missing_trial_does_not_disappear_from_metric_denominators():
    dataset, policy, manifest, trials = setup()
    policy = policy.model_copy(update={"repeats": 2})
    manifest.update(repeats=2, gate_policy_sha256=gate_policy_sha256(policy))
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert (
        result["observations"]["metric_coverage"]["no_severe_unsafe_guidance"]["denominator"] == 2
    )
    assert result["observations"]["risk_coverage"]["unsafe_guidance"]["value"] == 0.5


def test_invalid_policy_cannot_silently_disable_thresholds():
    _, policy, _, _ = setup()
    values = policy.model_dump()
    for patch in (
        {"metric_coverage": {}},
        {"risk_coverage": {"unsafe_guidance": float("nan")}},
        {"min_trial_decision_coverage": 0.0},
        {"mode": "contract"},
        {"split": "dev"},
    ):
        with pytest.raises(ValidationError):
            GatePolicy.model_validate(values | patch)


def _gate_cli_inputs(tmp_path):
    import hashlib
    import json

    dataset, policy, manifest, trials = setup()
    dataset_path = tmp_path / "cases.jsonl"
    dataset_path.write_text(dataset.cases[0].model_dump_json() + "\n", encoding="utf-8")
    policy = policy.model_copy(
        update={"dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest()}
    )
    manifest["dataset_sha256"] = policy.dataset_sha256
    manifest["gate_policy_sha256"] = gate_policy_sha256(policy)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(policy.model_dump_json(), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    trials_path = tmp_path / "trials.jsonl"
    trials_path.write_text("".join(json.dumps(t) + "\n" for t in trials), encoding="utf-8")
    return [
        "release-gate",
        "--dataset",
        str(dataset_path),
        "--manifest",
        str(manifest_path),
        "--trials",
        str(trials_path),
        "--policy",
        str(policy_path),
        "--no-quota-check",
    ]


def test_cli_uses_public_fail_closed_verifier(tmp_path, capsys):
    import json

    from app.evaluation.task_eval.cli import main

    args = _gate_cli_inputs(tmp_path)
    assert main(args) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert "ISOLATION_UNVERIFIED" in result["reasons"]
    assert result["observations"]["trial_coverage"]["numerator"] == 0


def test_cli_invalid_input_is_generic_and_does_not_leak(tmp_path, capsys):
    from app.evaluation.task_eval.cli import main

    args = _gate_cli_inputs(tmp_path)
    manifest_path = Path(args[args.index("--manifest") + 1])
    manifest_path.write_text('{"private-token": "super-secret",', encoding="utf-8")
    assert main(args) == 1
    result = capsys.readouterr()
    assert "super-secret" not in result.out + result.err
    assert "private-token" not in result.out + result.err
    assert "invalid release gate input" in result.err


def test_cli_rejects_duplicate_json_keys_without_leaking(tmp_path, capsys):
    from app.evaluation.task_eval.cli import main

    args = _gate_cli_inputs(tmp_path)
    manifest_path = Path(args[args.index("--manifest") + 1])
    manifest_path.write_text('{"key":"secret-value","key":"other"}', encoding="utf-8")
    assert main(args) == 1
    result = capsys.readouterr()
    assert "secret-value" not in result.out + result.err


def test_frozen_unanswerable_recall_na_matches_runner_without_counting_as_pass():
    from app.evaluation.task_eval.contracts import Assertion

    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    recall = Assertion(
        id="recall-na",
        phase="final",
        rule="required_evidence_recalled",
        required=True,
        parameters={"answerable": False, "relevant_ids": [], "forbidden_ids": [], "k": 5},
    )
    expected = case.expected.model_copy(update={"assertions": [*case.expected.assertions, recall]})
    dataset = replace(dataset, cases=[case.model_copy(update={"expected": expected})])
    trials[0]["assertions"].append(
        {
            "id": recall.id,
            "rule": recall.rule,
            "phase": recall.phase,
            "required": True,
            "applicable": True,
            "risk": None,
            "status": "NA",
        }
    )
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "PASS"
    assert "required_evidence_recalled" not in result["observations"]["metric_coverage"]
    assert result["observations"]["trial_coverage"]["value"] == 1
    assert assess_release_gate(dataset, manifest, trials, policy)["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "change", ["answerable", "relevant", "invalid_k", "risk", "forbidden_without_risk"]
)
def test_unanswerable_na_cannot_hide_applicable_or_risk_assertions(change):
    from app.evaluation.task_eval.contracts import Assertion

    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    params = {"answerable": False, "relevant_ids": [], "forbidden_ids": [], "k": 5}
    if change == "answerable":
        params["answerable"] = True
    elif change == "relevant":
        params["relevant_ids"] = ["unavailable"]
    elif change == "invalid_k":
        params["k"] = 0
    elif change == "forbidden_without_risk":
        params["forbidden_ids"] = ["private"]
    risk = "privacy" if change == "risk" else None
    recall = Assertion(
        id="recall-na",
        phase="final",
        rule="required_evidence_recalled",
        required=True,
        risk=risk,
        parameters=params,
    )
    expected = case.expected.model_copy(update={"assertions": [*case.expected.assertions, recall]})
    dataset = replace(dataset, cases=[case.model_copy(update={"expected": expected})])
    trials[0]["assertions"].append(
        {
            "id": recall.id,
            "rule": recall.rule,
            "phase": recall.phase,
            "required": True,
            "applicable": True,
            "risk": risk,
            "status": "NA",
        }
    )
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert any(r.startswith("INVALID_ASSERTION_APPLICABILITY") for r in result["reasons"])


def test_unanswerable_with_forbidden_ids_requires_separate_verified_privacy_assertion():
    from app.evaluation.task_eval.contracts import Assertion

    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    recall = Assertion(
        id="recall-na",
        phase="final",
        rule="required_evidence_recalled",
        required=True,
        parameters={"answerable": False, "relevant_ids": [], "forbidden_ids": ["private"], "k": 5},
    )
    privacy = Assertion(
        id="privacy",
        phase="final",
        rule="authorization_denied_no_leak",
        required=True,
        risk="privacy",
        parameters={},
    )
    expected = case.expected.model_copy(
        update={"assertions": [*case.expected.assertions, recall, privacy]}
    )
    dataset = replace(dataset, cases=[case.model_copy(update={"expected": expected})])
    for item, status in [(recall, "NA"), (privacy, "PASS")]:
        trials[0]["assertions"].append(
            {
                "id": item.id,
                "rule": item.rule,
                "phase": item.phase,
                "required": True,
                "applicable": True,
                "risk": item.risk,
                "status": status,
                "evidence_ref_sha256": ["d" * 64],
            }
        )
    policy = policy.model_copy(
        update={
            "metric_coverage": {**policy.metric_coverage, privacy.rule: 1.0},
            "risk_coverage": {**policy.risk_coverage, "privacy": 1.0},
        }
    )
    manifest["gate_policy_sha256"] = gate_policy_sha256(policy)
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "PASS"
    assert result["observations"]["risk_coverage"]["privacy"]["value"] == 1.0
    trials[0]["assertions"][-1]["status"] = "UNKNOWN"
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"


def test_unsafe_guidance_case_requires_applicable_required_clinical_assertion():
    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    original = case.expected.assertions[0]
    ordinary = original.model_copy(update={"rule": "semantic", "risk": None})
    expected = case.expected.model_copy(update={"assertions": [ordinary]})
    dataset = replace(dataset, cases=[case.model_copy(update={"expected": expected})])
    trials[0]["assertions"][0].update(rule="semantic", risk=None)
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert "CLINICAL_ASSERTION_MISSING:SAFE-015" in result["reasons"]


@pytest.mark.parametrize(
    "change",
    [
        "unrelated_rule_with_clinical_risk",
        "wrong_phase",
        "wrong_risk",
        "missing_mapping",
        "reversed_mapping",
        "missing_metric",
        "empty_prompt_version",
    ],
)
def test_clinical_case_cannot_pass_with_nonclinical_or_unfrozen_rule(change):
    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    original = case.expected.assertions[0]
    parameters = dict(original.parameters)
    updates = {}
    if change == "unrelated_rule_with_clinical_risk":
        updates["rule"] = "route_allowed"
        trials[0]["assertions"][0]["rule"] = "route_allowed"
        policy = policy.model_copy(update={"metric_coverage": {"route_allowed": 1.0}})
        manifest["gate_policy_sha256"] = gate_policy_sha256(policy)
    elif change == "wrong_phase":
        updates["phase"] = "before_decision"
        trials[0]["assertions"][0]["phase"] = "before_decision"
    elif change == "wrong_risk":
        updates["risk"] = None
        trials[0]["assertions"][0]["risk"] = None
    elif change == "missing_mapping":
        parameters.pop("score_mapping")
    elif change == "reversed_mapping":
        parameters["score_mapping"] = {"0": "PASS", "1": "UNKNOWN", "2": "FAIL"}
    elif change == "missing_metric":
        parameters.pop("metric_id")
    else:
        parameters["prompt_version"] = "  "
    updates["parameters"] = parameters
    updated_assertion = original.model_copy(update=updates)
    updated_expected = case.expected.model_copy(update={"assertions": [updated_assertion]})
    dataset = replace(dataset, cases=[case.model_copy(update={"expected": updated_expected})])
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert "CLINICAL_ASSERTION_MISSING:SAFE-015" in result["reasons"]


def test_clinical_risk_outside_safety_category_still_requires_safety_rule():
    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    assertion = case.expected.assertions[0].model_copy(update={"rule": "route_allowed"})
    updated_case = case.model_copy(
        update={
            "primary_scenario": "routing",
            "expected": case.expected.model_copy(update={"assertions": [assertion]}),
        }
    )
    dataset = replace(dataset, cases=[updated_case])
    trials[0]["primary_scenario"] = "routing"
    trials[0]["assertions"][0]["rule"] = "route_allowed"
    policy = policy.model_copy(update={"metric_coverage": {"route_allowed": 1.0}})
    manifest["gate_policy_sha256"] = gate_policy_sha256(policy)
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert "CLINICAL_ASSERTION_MISSING:SAFE-015" in result["reasons"]


def test_clinical_rule_requires_clinical_risk_even_outside_safety_category():
    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    assertion = case.expected.assertions[0].model_copy(update={"risk": None})
    updated_case = case.model_copy(
        update={
            "primary_scenario": "routing",
            "expected": case.expected.model_copy(update={"assertions": [assertion]}),
        }
    )
    dataset = replace(dataset, cases=[updated_case])
    trials[0]["primary_scenario"] = "routing"
    trials[0]["assertions"][0]["risk"] = None
    result = _assess(dataset, manifest, trials, policy, TestOnlyVerifier())
    assert result["status"] == "BLOCKED"
    assert "CLINICAL_ASSERTION_MISSING:SAFE-015" in result["reasons"]
