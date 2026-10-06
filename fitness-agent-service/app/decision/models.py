"""经营决策领域对象和状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PolicyMode(StrEnum):
    DISABLED = "DISABLED"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    ADVISORY = "ADVISORY"
    AUTONOMOUS = "AUTONOMOUS"


class RiskLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class DecisionCaseStatus(StrEnum):
    DETECTED = "DETECTED"
    DIAGNOSING = "DIAGNOSING"
    OPTIONS_READY = "OPTIONS_READY"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    EVALUATED = "EVALUATED"
    CLOSED = "CLOSED"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    POLICY_REJECTED = "POLICY_REJECTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


class ActionRunStatus(StrEnum):
    PLANNED = "PLANNED"
    AUTHORIZED = "AUTHORIZED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    COMPENSATION_REQUIRED = "COMPENSATION_REQUIRED"
    COMPENSATED = "COMPENSATED"
    CANCELLED = "CANCELLED"


ALLOWED_DECISION_ACTIONS = frozenset(
    {
        "CREATE_OPERATIONS_TASK",
        "CREATE_COACH_FOLLOW_UP",
    }
)


TERMINAL_CASE_STATUSES = {
    DecisionCaseStatus.CLOSED,
    DecisionCaseStatus.DATA_INSUFFICIENT,
    DecisionCaseStatus.POLICY_REJECTED,
    DecisionCaseStatus.APPROVAL_REJECTED,
    DecisionCaseStatus.EXPIRED,
}

_CASE_TRANSITIONS: dict[DecisionCaseStatus, frozenset[DecisionCaseStatus]] = {
    DecisionCaseStatus.DETECTED: frozenset(
        {
            DecisionCaseStatus.DIAGNOSING,
            DecisionCaseStatus.DATA_INSUFFICIENT,
            DecisionCaseStatus.SUSPENDED,
        }
    ),
    DecisionCaseStatus.DIAGNOSING: frozenset(
        {
            DecisionCaseStatus.OPTIONS_READY,
            DecisionCaseStatus.DATA_INSUFFICIENT,
            DecisionCaseStatus.SUSPENDED,
        }
    ),
    DecisionCaseStatus.OPTIONS_READY: frozenset(
        {DecisionCaseStatus.POLICY_EVALUATED, DecisionCaseStatus.SUSPENDED}
    ),
    DecisionCaseStatus.POLICY_EVALUATED: frozenset(
        {
            DecisionCaseStatus.WAITING_APPROVAL,
            DecisionCaseStatus.APPROVED,
            DecisionCaseStatus.POLICY_REJECTED,
            DecisionCaseStatus.CLOSED,
        }
    ),
    DecisionCaseStatus.WAITING_APPROVAL: frozenset(
        {
            DecisionCaseStatus.APPROVED,
            DecisionCaseStatus.APPROVAL_REJECTED,
            DecisionCaseStatus.EXPIRED,
        }
    ),
    DecisionCaseStatus.APPROVED: frozenset(
        {DecisionCaseStatus.EXECUTING, DecisionCaseStatus.SUSPENDED, DecisionCaseStatus.EXPIRED}
    ),
    DecisionCaseStatus.EXECUTING: frozenset(
        {
            DecisionCaseStatus.OBSERVING,
            DecisionCaseStatus.EXECUTION_FAILED,
            DecisionCaseStatus.SUSPENDED,
        }
    ),
    DecisionCaseStatus.EXECUTION_FAILED: frozenset(
        {DecisionCaseStatus.EXECUTING, DecisionCaseStatus.SUSPENDED, DecisionCaseStatus.CLOSED}
    ),
    DecisionCaseStatus.OBSERVING: frozenset(
        {DecisionCaseStatus.EVALUATED, DecisionCaseStatus.SUSPENDED}
    ),
    DecisionCaseStatus.EVALUATED: frozenset({DecisionCaseStatus.CLOSED}),
    DecisionCaseStatus.SUSPENDED: frozenset(
        {
            DecisionCaseStatus.DIAGNOSING,
            DecisionCaseStatus.WAITING_APPROVAL,
            DecisionCaseStatus.APPROVED,
            DecisionCaseStatus.CLOSED,
        }
    ),
}


def validate_case_transition(current: DecisionCaseStatus, target: DecisionCaseStatus) -> None:
    if target not in _CASE_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"不允许的决策状态转换：{current.value}->{target.value}")


class ObjectiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    direction: str = Field(pattern=r"^(INCREASE|DECREASE|TARGET)$")
    target_value: float | None = None
    guardrail_metrics: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("guardrail_metrics")
    @classmethod
    def unique_guardrails(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 64 for item in normalized):
            raise ValueError("护栏指标名称过长")
        return normalized

    @model_validator(mode="after")
    def target_direction_requires_value(self) -> ObjectiveInput:
        if self.direction == "TARGET" and self.target_value is None:
            raise ValueError("TARGET 目标必须提供 target_value")
        return self


class StrategyPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    mode: PolicyMode = PolicyMode.SHADOW
    risk_level: RiskLevel
    config: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: tuple[str, ...] = Field(min_length=1, max_length=30)
    budget_limit: float | None = Field(default=None, ge=0)
    max_affected_subjects: int = Field(default=1, ge=1, le=100_000)
    valid_to: datetime | None = None

    @field_validator("allowed_actions")
    @classmethod
    def validate_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        actions = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not actions or any(
            len(item) > 64 or not item.replace("_", "").isalnum() for item in actions
        ):
            raise ValueError("动作类型必须是非空的稳定标识")
        unknown = sorted(set(actions) - ALLOWED_DECISION_ACTIONS)
        if unknown:
            raise ValueError(f"策略包含未注册动作：{', '.join(unknown)}")
        return actions

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "history_periods",
            "minimum_samples",
            "relative_threshold",
            "z_threshold",
            "minimum_confidence",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"策略包含未知配置：{', '.join(unknown)}")
        checks = {
            "history_periods": (3, 365),
            "minimum_samples": (3, 365),
            "relative_threshold": (0.001, 10.0),
            "z_threshold": (0.1, 20.0),
            "minimum_confidence": (0.0, 1.0),
        }
        for key, raw in value.items():
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise TypeError(f"策略配置 {key} 必须是数字")
            lower, upper = checks[key]
            if not lower <= float(raw) <= upper:
                raise ValueError(f"策略配置 {key} 超出允许范围")
        return value

    @model_validator(mode="after")
    def validate_policy_window(self) -> StrategyPolicyInput:
        history = int(self.config.get("history_periods", 28))
        minimum = int(self.config.get("minimum_samples", 7))
        if minimum > history:
            raise ValueError("minimum_samples 不能大于 history_periods")
        if self.valid_to is not None and self.valid_to.tzinfo is None:
            raise ValueError("策略失效时间必须包含时区")
        return self


class MetricSnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=64)
    dimension_type: str = Field(default="ORGANIZATION", min_length=1, max_length=64)
    dimension_id: str = Field(min_length=1, max_length=128)
    bucket: str = Field(pattern=r"^(DAY|WEEK|MONTH)$")
    period_start: datetime
    period_end: datetime
    value: float
    sample_size: int = Field(default=0, ge=0)
    catalog_version: str = Field(min_length=1, max_length=128)
    source_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    quality_status: str = Field(default="PASSED", pattern=r"^(PASSED|STALE|INCOMPLETE|REJECTED)$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> MetricSnapshotInput:
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("指标快照时间必须包含时区")
        if self.period_end <= self.period_start:
            raise ValueError("指标快照结束时间必须晚于开始时间")
        return self


class DecisionOptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    title: str = Field(min_length=1, max_length=120)
    action_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    action_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=30)
    expected_benefit: float = 0
    estimated_cost: float = Field(default=0, ge=0)
    risk_penalty: float = Field(default=0, ge=0)
    uncertainty_penalty: float = Field(default=0, ge=0)
    confidence: float = Field(ge=0, le=1)
    model_name: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=128)

    @property
    def score(self) -> float:
        return round(
            self.expected_benefit
            - self.estimated_cost
            - self.risk_penalty
            - self.uncertainty_penalty,
            4,
        )


@dataclass(frozen=True)
class PolicyEvaluation:
    allowed: bool
    requires_approval: bool
    reason_code: str
    effective_mode: PolicyMode


@dataclass(frozen=True)
class DecisionCaseRecord:
    id: str
    organization_id: str
    objective_id: str
    policy_id: str
    anomaly_id: str
    title: str
    status: str
    risk_level: str
    diagnosis: dict[str, Any]
    selected_option_id: str | None
    trace_id: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DecisionOptionRecord:
    id: str
    case_id: str
    option_key: str
    title: str
    action_type: str
    action_payload: dict[str, Any]
    evidence_refs: tuple[str, ...]
    expected_benefit: float
    estimated_cost: float
    risk_penalty: float
    uncertainty_penalty: float
    score: float
    confidence: float
