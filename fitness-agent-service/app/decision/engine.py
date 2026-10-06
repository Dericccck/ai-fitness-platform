"""不依赖模型的异常、诊断、策略和实验算法。"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from .models import PolicyEvaluation, PolicyMode, RiskLevel

DETECTOR_VERSION = "robust-baseline-v1"


@dataclass(frozen=True)
class AnomalyAssessment:
    anomalous: bool
    reason_code: str
    severity: str
    actual: float
    expected: float | None
    deviation_percent: float | None
    robust_z_score: float | None
    evidence: dict[str, float | int | str | None]


def assess_anomaly(
    actual: float,
    history: Iterable[float],
    *,
    minimum_samples: int = 7,
    relative_threshold: float = 0.15,
    z_threshold: float = 3.5,
) -> AnomalyAssessment:
    """使用中位数和 MAD 判断异常，避免少量极端值污染基线。"""

    values = [float(value) for value in history if math.isfinite(float(value))]
    if len(values) < minimum_samples:
        return AnomalyAssessment(
            False,
            "INSUFFICIENT_BASELINE",
            "INFO",
            actual,
            None,
            None,
            None,
            {"sample_count": len(values), "minimum_samples": minimum_samples},
        )
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    relative = None if median == 0 else (actual - median) / abs(median)
    robust_z = None if mad == 0 else 0.6745 * (actual - median) / mad
    relative_hit = relative is not None and abs(relative) >= relative_threshold
    statistical_hit = robust_z is not None and abs(robust_z) >= z_threshold
    anomalous = relative_hit and (statistical_hit or mad == 0)
    magnitude = max(abs(relative or 0), abs(robust_z or 0) / max(z_threshold, 0.001))
    severity = "CRITICAL" if magnitude >= 2 else "HIGH" if magnitude >= 1.5 else "MEDIUM"
    return AnomalyAssessment(
        anomalous,
        "ROBUST_BASELINE_DEVIATION" if anomalous else "WITHIN_BASELINE",
        severity if anomalous else "INFO",
        actual,
        median,
        round(relative * 100, 4) if relative is not None else None,
        round(robust_z, 4) if robust_z is not None else None,
        {
            "sample_count": len(values),
            "median": median,
            "mad": mad,
            "relative_threshold": relative_threshold,
            "z_threshold": z_threshold,
            "detector_version": DETECTOR_VERSION,
        },
    )


def dimension_contributions(
    current: dict[str, float], baseline: dict[str, float]
) -> list[dict[str, float | str]]:
    """计算维度对总体变化的贡献；只陈述算术贡献，不声称因果。"""

    rows: list[dict[str, float | str]] = []
    for key in sorted(set(current) | set(baseline)):
        current_value = float(current.get(key, 0))
        baseline_value = float(baseline.get(key, 0))
        delta = current_value - baseline_value
        rows.append(
            {
                "dimension": key,
                "current": current_value,
                "baseline": baseline_value,
                "delta": delta,
            }
        )
    total_magnitude = sum(abs(float(row["delta"])) for row in rows)
    for row in rows:
        row["contribution_percent"] = (
            round(abs(float(row["delta"])) / total_magnitude * 100, 4)
            if total_magnitude > 0
            else 0.0
        )
    return sorted(rows, key=lambda item: abs(float(item["delta"])), reverse=True)


def evaluate_policy(
    *,
    mode: PolicyMode,
    risk_level: RiskLevel,
    action_type: str,
    allowed_actions: Iterable[str],
    affected_subjects: int,
    max_affected_subjects: int,
    estimated_cost: float,
    budget_limit: float | None,
    data_quality_passed: bool,
    confidence: float,
    minimum_confidence: float = 0.68,
) -> PolicyEvaluation:
    if mode == PolicyMode.DISABLED:
        return PolicyEvaluation(False, False, "POLICY_DISABLED", mode)
    if not data_quality_passed:
        return PolicyEvaluation(False, False, "DATA_QUALITY_REJECTED", mode)
    if action_type not in set(allowed_actions):
        return PolicyEvaluation(False, False, "ACTION_NOT_ALLOWED", mode)
    if affected_subjects < 1 or affected_subjects > max_affected_subjects:
        return PolicyEvaluation(False, False, "AFFECTED_SCOPE_EXCEEDED", mode)
    if budget_limit is not None and estimated_cost > budget_limit:
        return PolicyEvaluation(False, False, "BUDGET_EXCEEDED", mode)
    if confidence < minimum_confidence:
        return PolicyEvaluation(False, False, "CONFIDENCE_TOO_LOW", mode)
    if mode in {PolicyMode.REPLAY, PolicyMode.SHADOW}:
        return PolicyEvaluation(True, False, "NON_EXECUTING_MODE", mode)
    requires_approval = mode == PolicyMode.ADVISORY or risk_level in {
        RiskLevel.L2,
        RiskLevel.L3,
        RiskLevel.L4,
    }
    if risk_level == RiskLevel.L4:
        return PolicyEvaluation(False, True, "RISK_LEVEL_FORBIDS_AUTOMATION", mode)
    return PolicyEvaluation(
        True,
        requires_approval,
        "APPROVAL_REQUIRED" if requires_approval else "AUTONOMOUS_ALLOWED",
        mode,
    )


def stable_experiment_group(
    experiment_id: str, subject_key: str, salt: str, treatment_percent: int
) -> tuple[str, str]:
    if not 1 <= treatment_percent <= 99:
        raise ValueError("实验组比例必须位于 1 到 99 之间")
    subject_hash = hashlib.sha256(f"{experiment_id}:{subject_key}".encode()).hexdigest()
    assignment = int(hashlib.sha256(f"{salt}:{subject_hash}".encode()).hexdigest()[:8], 16) % 100
    return subject_hash, "TREATMENT" if assignment < treatment_percent else "CONTROL"


def incremental_effect(
    treatment_value: float,
    treatment_samples: int,
    control_value: float,
    control_samples: int,
) -> dict[str, float | int | None]:
    if treatment_samples < 1 or control_samples < 1:
        raise ValueError("实验组和对照组都必须有样本")
    absolute = treatment_value - control_value
    relative = None if control_value == 0 else absolute / abs(control_value) * 100
    return {
        "treatment_value": treatment_value,
        "treatment_samples": treatment_samples,
        "control_value": control_value,
        "control_samples": control_samples,
        "absolute_effect": round(absolute, 4),
        "relative_effect_percent": round(relative, 4) if relative is not None else None,
    }
