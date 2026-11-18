"""Versioned, strictly parsed contracts for offline task evaluation.

Case data is untrusted input. It contains logical references, never credentials.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrictModel):
    type: Literal["synthetic", "authorized_trial", "historical_deidentified"]
    reference: str = Field(min_length=1)
    deidentified: bool
    authorization: str | None = None
    retention: str | None = None

    @model_validator(mode="after")
    def authorized_source(self) -> Source:
        if self.type != "synthetic" and (
            not self.authorization or not self.retention or not self.deidentified
        ):
            raise ValueError(
                "non-synthetic sources require authorization, deidentification and retention"
            )
        return self


class Review(StrictModel):
    status: Literal["draft", "reviewed"]
    annotator_ref: str | None = None
    reviewer_ref: str | None = None
    reviewed_at: str | None = None
    decision_ref: str | None = None

    @model_validator(mode="after")
    def reviewed_has_evidence(self) -> Review:
        if self.status == "reviewed" and not all(
            (self.annotator_ref, self.reviewer_ref, self.reviewed_at, self.decision_ref)
        ):
            raise ValueError(
                "reviewed cases require actual annotation, review, date and decision references"
            )
        return self


class Fixture(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    namespace_isolated: bool


class Actor(StrictModel):
    role: Literal["STUDENT", "COACH", "ADMIN", "CUSTOMER_SERVICE"]
    organization_ref: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)


class Clock(StrictModel):
    mode: Literal["injected", "real"]
    timezone: str = Field(min_length=1)
    business_time: str | None = None

    @model_validator(mode="after")
    def check_time(self) -> Clock:
        if self.mode == "injected" and not self.business_time:
            raise ValueError("injected clock requires business_time")
        return self


class Trigger(StrictModel):
    type: Literal["user", "event"]


class Step(StrictModel):
    type: Literal["chat", "assert", "decision", "inject_fault", "await_terminal", "trigger_event"]
    message: str | None = None
    message_ref: str | None = None
    capture: list[str] = Field(default_factory=list)
    phase: str | None = None
    decision: Literal["APPROVE", "REJECT"] | None = None
    confirmation_ref: str | None = None
    target: str | None = None
    expected_authorization_status: str | None = None
    expected_execution_status: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    fault: str | None = None
    event_ref: str | None = None

    @model_validator(mode="after")
    def required_fields(self) -> Step:
        if self.type == "chat" and bool(self.message) == bool(self.message_ref):
            raise ValueError("chat needs exactly one of message and message_ref")
        if self.type == "assert" and not self.phase:
            raise ValueError("assert needs phase")
        if self.type == "decision" and not (self.decision and self.confirmation_ref):
            raise ValueError("decision needs decision and confirmation_ref")
        if self.type == "inject_fault" and not self.fault:
            raise ValueError("inject_fault needs fault")
        if self.type == "await_terminal" and not (self.target and self.timeout_seconds):
            raise ValueError("await_terminal needs target and timeout_seconds")
        if self.type == "trigger_event" and not self.event_ref:
            raise ValueError("trigger_event needs event_ref")
        return self


class Assertion(StrictModel):
    id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    required: bool
    applicable: bool = True
    risk: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class Expected(StrictModel):
    allowed_routes: list[str]
    assertions: list[Assertion] = Field(min_length=1)
    answer_rubric_ref: str | None = None
    reference_points: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_assertions(self) -> Expected:
        ids = [a.id for a in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate assertion id")
        if not any(a.required and a.applicable for a in self.assertions):
            raise ValueError("at least one required applicable assertion is needed")
        return self


class Timeouts(StrictModel):
    step_seconds: float = Field(gt=0)
    trial_seconds: float = Field(gt=0)
    settle_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def check_limits(self) -> Timeouts:
        if self.step_seconds > self.trial_seconds:
            raise ValueError("step timeout exceeds trial timeout")
        return self


class Metadata(StrictModel):
    risk_level: Literal["low", "medium", "high"]
    grader_version: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    notes: str = ""


class TaskCase(StrictModel):
    schema_version: Literal["1.0"]
    case_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    primary_scenario: Literal["routing", "knowledge", "booking", "operations", "safety"]
    subcategory: str = Field(min_length=1)
    tags: list[str]
    capability_status: Literal["supported", "planned"]
    delivery_scope: Literal["in_scope", "out_of_scope"]
    source: Source
    review: Review
    fixture: Fixture
    actor: Actor
    clock: Clock
    trigger: Trigger
    steps: list[Step] = Field(min_length=1)
    expected: Expected
    timeouts: Timeouts
    metadata: Metadata

    @model_validator(mode="after")
    def validate_phases(self) -> TaskCase:
        phases = {step.phase for step in self.steps if step.type == "assert"}
        missing = {a.phase for a in self.expected.assertions} - phases
        if missing:
            raise ValueError(
                f"assertion phases have no corresponding assert step: {sorted(missing)}"
            )
        return self
