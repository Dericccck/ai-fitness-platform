from app.evaluation.task_eval.report import reduce_case, reduce_trial, summarize


def assertion(status, *, required=True, risk=None):
    return {"rule": "same", "status": status, "required": required, "risk": risk}


def test_pre_execution_error_and_later_incomplete_and_risk_failure():
    missing = [assertion("UNKNOWN")]
    assert reduce_trial(missing, executed=False, failure="ERROR") == "ERROR"
    assert reduce_trial(missing, executed=True, failure="ERROR") == "INCONCLUSIVE"
    assert (
        reduce_trial(
            [assertion("FAIL", required=False, risk="leak")], executed=True, failure="ERROR"
        )
        == "FAIL"
    )
    assert (
        reduce_trial(
            [assertion("PASS"), assertion("UNKNOWN", required=False, risk="leak")], executed=True
        )
        == "INCONCLUSIVE"
    )


def test_risk_failures_are_not_hidden_and_counted_once_per_trial():
    trials = [
        {
            "case_id": "c1",
            "trial_index": 0,
            "delivery_scope": "in_scope",
            "primary_scenario": "safety",
            "capability_status": "supported",
            "status": "FAIL",
            "executed": True,
            "assertions": [
                assertion("PASS"),
                assertion("FAIL", required=False, risk="leak"),
                assertion("FAIL", required=False, risk="leak"),
            ],
        }
    ]
    summary = summarize(trials, 1)
    assert summary["metrics"]["same"]["counts"]["FAIL"] == 1
    assert summary["risks"]["leak"]["violations"] == 1
    assert summary["risks"]["leak"]["decidable_exposures"] == 1
    assert summary["risks"]["leak"]["unknown"] == 0


def test_case_pass_requires_unique_executed_trial_indexes():
    passed = {"trial_index": 0, "status": "PASS", "executed": True}
    assert reduce_case([passed, passed], 2) == "INCONCLUSIVE"
    assert reduce_case([passed | {"executed": False}], 1) == "INCONCLUSIVE"
    assert reduce_case([passed, passed | {"trial_index": 1}], 2) == "PASS"
    assert reduce_case([passed, {"trial_index": 0, "status": "FAIL"}], 2) == "FAIL"
