from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.infrastructure.agent_context import AgentContextVerifier
from scripts.evidence_statistics import (
    approximate_users_per_group,
    describe,
    paired_change,
    percentile,
    renewal_effect,
    wilson,
)
from scripts.project_evidence_benchmark import history_metadata, signed_context


def test_summary_and_pairing_do_not_hide_regressions() -> None:
    assert percentile([1, 2, 3], 0.95) == pytest.approx(2.9)
    assert describe([])["mean"] is None
    change = paired_change([1, 2, 3], [2, 3, 4])
    assert change["mean_reduction"] == -1
    assert change["paired_mean_reduction_ci95"] == [-1, -1]
    with pytest.raises(ValueError):
        paired_change([1, 2], [3])


@pytest.mark.parametrize(
    "successes,total", [(4, 3), (-1, 3), (1, 0), (True, 3), (None, 3), (1.2, 3)]
)
def test_wilson_rejects_invalid_counts(successes: object, total: object) -> None:
    with pytest.raises(ValueError):
        wilson(successes, total)  # type: ignore[arg-type]


def test_wilson_reference_and_zero_successes() -> None:
    assert wilson(50, 100) == pytest.approx((0.4038315303659956, 0.5961684696340044))
    low, high = wilson(0, 100)
    assert low == 0 and high == pytest.approx(0.03699349820698568)


def test_no_data_or_synthetic_data_cannot_create_renewal_uplift() -> None:
    assert renewal_effect({}) == {"status": "DATA_INSUFFICIENT", "renewal_uplift_pp": None}
    result = renewal_effect(
        {
            "data_kind": "synthetic",
            "control": {"eligible_users": 100, "net_paid_renewals": 30},
            "treatment": {"eligible_users": 100, "net_paid_renewals": 40},
        }
    )
    assert result["observed_difference_pp"] == pytest.approx(10)
    assert result["status"] == "DESCRIPTIVE_ONLY"
    assert result["renewal_uplift_pp"] is None
    assert result["difference_ci95_pp"][0] < 0


def test_declared_randomization_still_requires_evidence_audit() -> None:
    data = {
        "data_kind": "real_observed",
        "source_reference": "audited-dataset-v1",
        "observation_start": "2026-01-01",
        "observation_end": "2026-03-01",
        "analysis": "ITT",
        "independent_user_randomization": True,
        "assignment_before_intervention": True,
        "same_mature_observation_window": True,
        "refund_window_closed": True,
        "sample_ratio_check_passed": True,
        "control": {"eligible_users": 1000, "net_paid_renewals": 300},
        "treatment": {"eligible_users": 1000, "net_paid_renewals": 400},
    }
    assert renewal_effect(data)["positive_interval"] is True
    assert renewal_effect(data)["status"] == "RCT_DECLARED_REQUIRES_AUDIT"
    assert renewal_effect(data)["renewal_uplift_pp"] is None
    data["independent_user_randomization"] = False
    assert renewal_effect(data)["status"] == "DESCRIPTIVE_ONLY"


def test_fixture_is_labelled_before_search_without_prefilled_results() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "evals/rag_runtime_benchmark.json").read_text()
    )
    allowed = {doc["id"] for doc in fixture["documents"] if not doc.get("access")}
    assert len(fixture["cases"]) == 26
    assert len(fixture["documents"]) == 34
    for case in fixture["cases"]:
        assert set(case["relevant"]).issubset(allowed)
        assert "retrieved_ids" not in case
    assert {doc["access"] for doc in fixture["documents"] if doc.get("access")} == {
        "private",
        "other_org",
        "role",
        "archived",
    }


def test_temporary_test_signature_and_history_provenance() -> None:
    token = signed_context("unique-ephemeral-test-secret")
    verifier = AgentContextVerifier("unique-ephemeral-test-secret", signing_key_id="evidence")
    identity = verifier.verify(token)
    assert identity.organization_ids == frozenset({"evidence-org"})
    assert identity.subject.startswith("evidence-")
    assert len(history_metadata()["function_sha256"]) == 64


def test_experiment_sample_planning_is_not_a_business_measurement() -> None:
    assert approximate_users_per_group(0.3, 0.35) == 1377
    with pytest.raises(ValueError):
        approximate_users_per_group(0.3, 0.3)
