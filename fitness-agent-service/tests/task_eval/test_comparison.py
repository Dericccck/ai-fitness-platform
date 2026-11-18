import pytest

from app.evaluation.task_eval.comparison import IncompatibleRuns, compare_runs


def manifest(**changes):
    data = {
        "mode": "contract",
        "adapter": "stub",
        "result_origin": "synthetic",
        "seed": 42,
        "dataset_sha256": "a" * 64,
        "dataset_version": "v1",
        "selection": {"split": "dev"},
        "selected_case_ids": ["a", "b"],
        "fixture_sha256": "b" * 64,
        "grader_version": "v1",
        "repeats": 2,
        "revision": "rev-1",
    }
    return data | changes


def trial(case, index, status):
    return {
        "case_id": case,
        "trial_index": index,
        "status": status,
        "seed": 42 + index,
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


def test_pairing_preserves_failure_and_missing_trials():
    baseline = [
        trial("a", 0, "PASS"),
        trial("a", 1, "PASS"),
        trial("b", 0, "FAIL"),
        trial("b", 1, "ERROR"),
    ]
    candidate = [trial("a", 0, "FAIL"), trial("a", 1, "PASS"), trial("b", 0, "PASS")]
    report = compare_runs(manifest(), baseline, manifest(), candidate)
    assert report["counts"] == {"improved": 0, "regressed": 1, "unchanged": 0, "incomparable": 1}
    assert report["cases"][1]["baseline"] == "FAIL"


@pytest.mark.parametrize(
    "change",
    [{"mode": "model_sandbox"}, {"fixture_sha256": "unknown"}, {"selection": {"split": "test"}}],
)
def test_rejects_unproven_or_different_conditions(change):
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [], manifest(**change), [])


def test_rejects_duplicate_trial():
    rows = [trial("a", 0, "PASS"), trial("a", 0, "FAIL")]
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), rows, manifest(), [])


def test_case_missing_from_both_runs_remains_incomparable():
    report = compare_runs(manifest(), [], manifest(), [])
    assert report["counts"]["incomparable"] == 2


def test_decidable_result_requires_execution_and_evidence():
    row = trial("a", 0, "PASS")
    row["executed"] = False
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [row], manifest(), [])
    row = trial("a", 0, "FAIL")
    row["assertions"][0].pop("evidence_ref_sha256")
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [row], manifest(), [])


def test_trial_seed_must_match_planned_repeat():
    row = trial("a", 0, "PASS")
    row["seed"] = 99
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [row], manifest(), [])


def test_vary_must_be_declared_unique_and_present():
    candidate = manifest(revision="rev-2")
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [], candidate, [])
    result = compare_runs(manifest(), [], candidate, [], vary="revision")
    assert result["varied_condition"] == {
        "name": "revision",
        "baseline": "rev-1",
        "candidate": "rev-2",
    }
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [], manifest(), [], vary="revision")
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [], candidate, [], vary="model")


def test_sandbox_context_must_be_known_and_only_one_variable_may_change():
    sandbox = manifest(
        mode="integration_sandbox",
        model="m1",
        prompt_version="p1",
        tool_schema_version="t1",
        index_version="i1",
        environment="isolated",
    )
    candidate = sandbox | {"model": "m2"}
    result = compare_runs(sandbox, [], candidate, [], vary="model")
    assert result["varied_condition"]["candidate"] == "m2"
    with pytest.raises(IncompatibleRuns):
        compare_runs(sandbox, [], candidate | {"index_version": "i2"}, [], vary="model")
    with pytest.raises(IncompatibleRuns):
        compare_runs(
            sandbox | {"environment": "unknown"}, [], sandbox | {"environment": "unknown"}, []
        )


def test_compare_rejects_different_assertion_contracts_and_invalid_hashes():
    baseline = trial("a", 0, "PASS")
    candidate = trial("a", 0, "PASS")
    candidate["assertions"][0]["rule"] = "another_rule"
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [baseline], manifest(), [candidate])
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(fixture_sha256="bogus"), [], manifest(fixture_sha256="bogus"), [])


def test_inapplicable_assertion_cannot_produce_pass():
    row = trial("a", 0, "PASS")
    row["assertions"][0]["applicable"] = False
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [row], manifest(), [row])


def test_necessary_applicable_na_is_incomparable_without_frozen_parameters():
    left = trial("a", 0, "PASS")
    left["assertions"].append(
        {
            "id": "recall",
            "rule": "required_evidence_recalled",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "NA",
            "reason": "UNANSWERABLE_RECALL_NA",
        }
    )
    result = compare_runs(
        manifest(repeats=1, selected_case_ids=["a"]),
        [left],
        manifest(repeats=1, selected_case_ids=["a"]),
        [left],
    )
    case = result["cases"][0]
    assert result["counts"]["incomparable"] == 1
    assert case["trials"][0]["baseline"] == "PASS"
    assert case["trials"][0]["baseline_comparability_reason"] == (
        "APPLICABLE_NA_REQUIRES_DATASET_VERIFICATION"
    )


def test_inapplicable_na_does_not_block_comparison():
    row = trial("a", 0, "PASS")
    row["assertions"].append(
        {
            "id": "not_applicable",
            "rule": "unused",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": False,
            "status": "NA",
        }
    )
    report = compare_runs(
        manifest(repeats=1, selected_case_ids=["a"]),
        [row],
        manifest(repeats=1, selected_case_ids=["a"]),
        [row],
    )
    assert report["counts"]["unchanged"] == 1


def test_observed_failure_survives_other_applicable_na():
    row = trial("a", 0, "FAIL")
    row["assertions"].append(
        {
            "id": "unknown",
            "rule": "some_rule",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "NA",
        }
    )
    report = compare_runs(
        manifest(repeats=1, selected_case_ids=["a"]),
        [row],
        manifest(repeats=1, selected_case_ids=["a"]),
        [row],
    )
    assert report["counts"]["unchanged"] == 1
    assert report["cases"][0]["baseline"] == "FAIL"


@pytest.mark.parametrize("field", ["trial", "assertion"])
def test_invalid_unhashable_status_is_an_incompatible_run(field):
    row = trial("a", 0, "PASS")
    if field == "trial":
        row["status"] = []
    else:
        row["assertions"][0]["status"] = []
    with pytest.raises(IncompatibleRuns):
        compare_runs(manifest(), [row], manifest(), [])
