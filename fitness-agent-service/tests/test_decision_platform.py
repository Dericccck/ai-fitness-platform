from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.decision.engine import (
    assess_anomaly,
    dimension_contributions,
    evaluate_policy,
    incremental_effect,
    stable_experiment_group,
)
from app.decision.models import (
    DecisionCaseStatus,
    DecisionOptionInput,
    MetricSnapshotInput,
    PolicyMode,
    RiskLevel,
    validate_case_transition,
)
from app.decision.service import DecisionPlatformService


def test_robust_detector_requires_baseline_and_detects_material_change() -> None:
    insufficient = assess_anomaly(40, [50, 51], minimum_samples=3)
    assert insufficient.anomalous is False
    assert insufficient.reason_code == "INSUFFICIENT_BASELINE"

    normal = assess_anomaly(52, [49, 50, 51, 50, 49, 51, 50])
    assert normal.anomalous is False

    changed = assess_anomaly(30, [50, 50, 50, 50, 50, 50, 50])
    assert changed.anomalous is True
    assert changed.deviation_percent == -40
    assert changed.severity in {"MEDIUM", "HIGH", "CRITICAL"}


def test_dimension_diagnosis_is_deterministic_and_does_not_claim_causality() -> None:
    rows = dimension_contributions({"morning": 40, "evening": 20}, {"morning": 45, "evening": 40})
    assert [row["dimension"] for row in rows] == ["evening", "morning"]
    assert rows[0]["delta"] == -20
    assert round(sum(float(row["contribution_percent"]) for row in rows), 4) == 100


def test_policy_blocks_scope_budget_quality_and_high_risk_automation() -> None:
    allowed = evaluate_policy(
        mode=PolicyMode.AUTONOMOUS,
        risk_level=RiskLevel.L1,
        action_type="CREATE_OPERATIONS_TASK",
        allowed_actions=("CREATE_OPERATIONS_TASK",),
        affected_subjects=1,
        max_affected_subjects=10,
        estimated_cost=0,
        budget_limit=10,
        data_quality_passed=True,
        confidence=0.9,
    )
    assert allowed.allowed is True
    assert allowed.requires_approval is False

    too_many = evaluate_policy(
        mode=PolicyMode.AUTONOMOUS,
        risk_level=RiskLevel.L1,
        action_type="CREATE_OPERATIONS_TASK",
        allowed_actions=("CREATE_OPERATIONS_TASK",),
        affected_subjects=11,
        max_affected_subjects=10,
        estimated_cost=0,
        budget_limit=10,
        data_quality_passed=True,
        confidence=0.9,
    )
    assert too_many.reason_code == "AFFECTED_SCOPE_EXCEEDED"

    forbidden = evaluate_policy(
        mode=PolicyMode.AUTONOMOUS,
        risk_level=RiskLevel.L4,
        action_type="CREATE_OPERATIONS_TASK",
        allowed_actions=("CREATE_OPERATIONS_TASK",),
        affected_subjects=1,
        max_affected_subjects=10,
        estimated_cost=0,
        budget_limit=10,
        data_quality_passed=True,
        confidence=0.9,
    )
    assert forbidden.allowed is False
    assert forbidden.requires_approval is True


def test_shadow_mode_never_requires_or_authorizes_execution() -> None:
    result = evaluate_policy(
        mode=PolicyMode.SHADOW,
        risk_level=RiskLevel.L1,
        action_type="CREATE_OPERATIONS_TASK",
        allowed_actions=("CREATE_OPERATIONS_TASK",),
        affected_subjects=1,
        max_affected_subjects=10,
        estimated_cost=0,
        budget_limit=None,
        data_quality_passed=True,
        confidence=0.8,
    )
    assert result.allowed is True
    assert result.reason_code == "NON_EXECUTING_MODE"
    assert result.requires_approval is False


def test_case_state_machine_rejects_skipping_authorization() -> None:
    validate_case_transition(DecisionCaseStatus.DETECTED, DecisionCaseStatus.DIAGNOSING)
    with pytest.raises(ValueError, match="不允许"):
        validate_case_transition(DecisionCaseStatus.DETECTED, DecisionCaseStatus.EXECUTING)


def test_option_score_is_programmatic() -> None:
    option = DecisionOptionInput(
        option_key="CREATE_TASK",
        title="创建任务",
        action_type="CREATE_OPERATIONS_TASK",
        expected_benefit=20,
        estimated_cost=2,
        risk_penalty=3,
        uncertainty_penalty=1,
        confidence=0.8,
    )
    assert option.score == 14


def test_snapshot_rejects_invalid_window_and_extra_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MetricSnapshotInput(
            metric="APPOINTMENT_COUNT",
            dimension_id="org-1",
            bucket="DAY",
            period_start=now,
            period_end=now - timedelta(days=1),
            value=1,
            catalog_version="catalog-v1",
            source_digest="a" * 64,
            sql="SELECT *",  # type: ignore[call-arg]
        )


def test_experiment_assignment_is_stable_and_effect_handles_zero_control() -> None:
    first = stable_experiment_group("exp-1", "user-1", "salt", 50)
    second = stable_experiment_group("exp-1", "user-1", "salt", 50)
    assert first == second
    assert first[1] in {"TREATMENT", "CONTROL"}

    effect = incremental_effect(0.2, 100, 0, 100)
    assert effect["absolute_effect"] == 0.2
    assert effect["relative_effect_percent"] is None


def test_aggregate_snapshot_only_generates_supported_organization_actions() -> None:
    policy = {
        "id": "policy-1",
        "organization_id": "org-1",
        "objective_id": "objective-1",
        "allowed_actions": [
            "SEND_IN_APP_REMINDER",
            "CREATE_OPERATIONS_TASK",
            "PROPOSE_BOOKING_ACTION",
        ],
    }

    options = DecisionPlatformService._default_options(policy, -25)

    assert [option.action_type for option in options] == ["CREATE_OPERATIONS_TASK"]
    assert options[0].action_payload["title"] == "创建运营跟进任务"
