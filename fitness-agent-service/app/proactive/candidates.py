"""主动式 Agent 的候选、决策和安全契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CandidateType(StrEnum):
    TRAINING_ADHERENCE = "TRAINING_ADHERENCE"
    TRAINING_PLAN_MISMATCH = "TRAINING_PLAN_MISMATCH"
    TRAINING_CYCLE_REVIEW = "TRAINING_CYCLE_REVIEW"
    COACH_STUDENT_ATTENTION = "COACH_STUDENT_ATTENTION"
    COACH_DAILY_DIGEST = "COACH_DAILY_DIGEST"
    OPERATIONS_DAILY_DIGEST = "OPERATIONS_DAILY_DIGEST"
    APPOINTMENT_FOLLOW_UP = "APPOINTMENT_FOLLOW_UP"
    CUSTOMER_SERVICE_FOLLOW_UP = "CUSTOMER_SERVICE_FOLLOW_UP"


class ProactiveDecisionType(StrEnum):
    IGNORE = "IGNORE"
    DEFER = "DEFER"
    NOTIFY = "NOTIFY"
    ASK_USER = "ASK_USER"
    PROPOSE_ACTION = "PROPOSE_ACTION"
    ESCALATE_TO_COACH = "ESCALATE_TO_COACH"


class RecommendedAction(StrEnum):
    NONE = "NONE"
    OPEN_TRAINING_PLAN = "OPEN_TRAINING_PLAN"
    ADJUST_TRAINING_PLAN = "ADJUST_TRAINING_PLAN"
    RESUME_TRAINING = "RESUME_TRAINING"
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    CONTACT_COACH = "CONTACT_COACH"
    OPEN_CUSTOMER_SERVICE = "OPEN_CUSTOMER_SERVICE"
    REVIEW_STUDENTS = "REVIEW_STUDENTS"
    REVIEW_OPERATIONS = "REVIEW_OPERATIONS"


class ProactiveAgentOutput(BaseModel):
    """LLM 只能产生这个对象；身份、机构、渠道和工具参数不属于模型输出。"""

    model_config = ConfigDict(extra="forbid")

    decision: ProactiveDecisionType
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    strategy: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    recommended_action: RecommendedAction = RecommendedAction.NONE
    confidence: float = Field(ge=0, le=1)
    defer_minutes: int = Field(default=0, ge=0, le=10_080)
    title: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=600)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_action(self) -> ProactiveAgentOutput:
        if self.decision == ProactiveDecisionType.DEFER and self.defer_minutes < 1:
            raise ValueError("DEFER 决策必须提供正数 defer_minutes")
        if self.decision in {
            ProactiveDecisionType.NOTIFY,
            ProactiveDecisionType.ASK_USER,
            ProactiveDecisionType.PROPOSE_ACTION,
            ProactiveDecisionType.ESCALATE_TO_COACH,
        } and (not self.title.strip() or not self.body.strip()):
            raise ValueError("通知类决策必须包含标题和正文")
        if self.decision in {ProactiveDecisionType.IGNORE, ProactiveDecisionType.DEFER}:
            self.title = ""
            self.body = ""
        return self

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError("证据引用无效")
        return tuple(dict.fromkeys(item.strip() for item in value))


@dataclass(frozen=True)
class CandidateRecord:
    id: str
    candidate_type: str
    reason_code: str
    subject_user_id: str
    organization_id: str
    aggregate_type: str
    aggregate_id: str
    trigger_source: str
    trigger_event_ids: tuple[str, ...]
    facts: dict[str, Any]
    dedupe_key: str
    priority: str
    status: str
    available_at: datetime
    expires_at: datetime
    attempt_count: int
    locked_by: str | None
    locked_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProactiveMessageSnapshot:
    id: str
    candidate_id: str
    channel: str
    template_key: str
    template_version: int
    title: str
    body: str
    cta_type: str | None
    cta_payload: dict[str, Any]
    content_source: str


_MEDICAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"诊断|治疗|药物|用药|处方|康复治疗|保证减重|保证治愈",
        r"diagnos|treat(?:ment)?|medication|prescri|guaranteed weight loss|cure",
    )
)
_MANIPULATIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"懒惰|失败者|必须马上|否则.*后果|羞耻", r"lazy|failure|must immediately|shame")
)


def validate_message_safety(output: ProactiveAgentOutput) -> str:
    """拒绝医疗承诺、施压和越权文本；返回稳定审计状态。"""

    text = f"{output.title}\n{output.body}"
    if any(pattern.search(text) for pattern in _MEDICAL_PATTERNS):
        raise ValueError("PROACTIVE_MEDICAL_CONTENT_REJECTED")
    if any(pattern.search(text) for pattern in _MANIPULATIVE_PATTERNS):
        raise ValueError("PROACTIVE_MANIPULATIVE_CONTENT_REJECTED")
    return "PASSED"


def candidate_expiry(candidate_type: CandidateType, now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    hours = {
        CandidateType.TRAINING_ADHERENCE: 72,
        CandidateType.TRAINING_PLAN_MISMATCH: 96,
        CandidateType.TRAINING_CYCLE_REVIEW: 168,
        CandidateType.COACH_STUDENT_ATTENTION: 48,
        CandidateType.COACH_DAILY_DIGEST: 24,
        CandidateType.OPERATIONS_DAILY_DIGEST: 24,
        CandidateType.APPOINTMENT_FOLLOW_UP: 24,
        CandidateType.CUSTOMER_SERVICE_FOLLOW_UP: 72,
    }[candidate_type]
    return current + timedelta(hours=hours)


NOTIFICATION_TYPE_BY_CANDIDATE: dict[CandidateType, str] = {
    CandidateType.TRAINING_ADHERENCE: "TRAINING_ADHERENCE_SUPPORT",
    CandidateType.TRAINING_PLAN_MISMATCH: "TRAINING_PLAN_ADJUSTMENT_SUGGESTED",
    CandidateType.TRAINING_CYCLE_REVIEW: "TRAINING_CYCLE_REVIEW",
    CandidateType.COACH_STUDENT_ATTENTION: "COACH_STUDENT_ATTENTION",
    CandidateType.COACH_DAILY_DIGEST: "COACH_DAILY_DIGEST",
    CandidateType.OPERATIONS_DAILY_DIGEST: "OPERATIONS_DAILY_DIGEST",
    CandidateType.APPOINTMENT_FOLLOW_UP: "APPOINTMENT_FOLLOW_UP",
    CandidateType.CUSTOMER_SERVICE_FOLLOW_UP: "CUSTOMER_SERVICE_FOLLOW_UP",
}
