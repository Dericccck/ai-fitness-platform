from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.model_gateway import JsonModelTurn
from app.proactive.candidate_engine import _candidate_specs
from app.proactive.candidates import (
    CandidateRecord,
    ProactiveAgentOutput,
    validate_message_safety,
)
from app.proactive.decision_agent import ProactiveDecisionAgent, deterministic_fallback
from app.proactive.events import ProactiveEventMessage


def _candidate(candidate_type: str = "TRAINING_PLAN_MISMATCH") -> CandidateRecord:
    now = datetime.now(UTC)
    return CandidateRecord(
        id="candidate-1",
        candidate_type=candidate_type,
        reason_code="REPEATED_TRAINING_SKIPS",
        subject_user_id="student-1",
        organization_id="org-1",
        aggregate_type="TRAINING_PLAN",
        aggregate_id="plan-1",
        trigger_source="SCHEDULE",
        trigger_event_ids=("event-1", "event-2"),
        facts={"skip_count": 2},
        dedupe_key="plan-mismatch:plan-1:week-1",
        priority="HIGH",
        status="PROCESSING",
        available_at=now,
        expires_at=now + timedelta(days=2),
        attempt_count=0,
        locked_by="worker-1",
        locked_at=now,
        last_error_code=None,
        created_at=now,
        updated_at=now,
    )


class _Settings:
    llm_model = "test-model"


class _UnavailableModel:
    settings = _Settings()

    async def chat_json_with_usage(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("provider unavailable")


class _UnsafeModel:
    settings = _Settings()

    async def chat_json_with_usage(self, *args: object, **kwargs: object) -> JsonModelTurn:
        return JsonModelTurn(
            content=ProactiveAgentOutput(
                decision="NOTIFY",
                reason_code="TRAINING_INACTIVE",
                strategy="UNSAFE",
                confidence=0.95,
                title="训练提醒",
                body="这个方案保证治愈你的疼痛。",
                evidence_refs=("candidate:candidate-1",),
            ).model_dump_json()
        )


@pytest.mark.asyncio
async def test_agent_uses_safe_fallback_when_model_is_unavailable() -> None:
    result = await ProactiveDecisionAgent(_UnavailableModel()).decide(_candidate())  # type: ignore[arg-type]

    assert result.content_source == "FALLBACK"
    assert result.output.decision == "PROPOSE_ACTION"
    assert result.output.recommended_action == "ADJUST_TRAINING_PLAN"
    assert "event:event-1" in result.output.evidence_refs


@pytest.mark.asyncio
async def test_agent_replaces_unsafe_model_content_with_safe_fallback() -> None:
    result = await ProactiveDecisionAgent(_UnsafeModel()).decide(_candidate())  # type: ignore[arg-type]

    assert result.content_source == "FALLBACK"
    assert result.safety_status == "FALLBACK"
    assert "保证治愈" not in result.output.body


def test_fallback_covers_all_supported_candidate_types() -> None:
    candidate_types = (
        "TRAINING_ADHERENCE",
        "TRAINING_PLAN_MISMATCH",
        "TRAINING_CYCLE_REVIEW",
        "COACH_STUDENT_ATTENTION",
        "COACH_DAILY_DIGEST",
        "OPERATIONS_DAILY_DIGEST",
        "APPOINTMENT_FOLLOW_UP",
        "CUSTOMER_SERVICE_FOLLOW_UP",
    )
    for candidate_type in candidate_types:
        output = deterministic_fallback(_candidate(candidate_type))
        assert output.title
        assert output.body
        assert validate_message_safety(output) == "PASSED"


def test_medical_and_manipulative_content_is_rejected() -> None:
    medical = ProactiveAgentOutput.model_validate(
        {
            "decision": "NOTIFY",
            "reason_code": "TEST",
            "strategy": "TEST",
            "recommended_action": "NONE",
            "confidence": 0.9,
            "title": "训练建议",
            "body": "这可以保证治愈你的疼痛。",
        }
    )
    with pytest.raises(ValueError, match="MEDICAL"):
        validate_message_safety(medical)


def test_skipped_training_event_generates_student_and_coach_candidates() -> None:
    event = ProactiveEventMessage.model_validate(
        {
            "event_id": "event-1",
            "source": "training",
            "event_type": "TRAINING_DAY_SKIPPED",
            "aggregate_id": "plan-1",
            "organization_id": "org-1",
            "aggregate_version": 2,
            "payload": {
                "planId": "plan-1",
                "dayId": "day-1",
                "studentId": "student-1",
                "coachId": "coach-1",
                "status": "SKIPPED",
            },
        }
    )

    specs = _candidate_specs(event)

    assert [item["candidate_type"].value for item in specs] == [  # type: ignore[union-attr]
        "TRAINING_ADHERENCE",
        "COACH_STUDENT_ATTENTION",
    ]


def test_cancelled_appointment_generates_follow_up_candidate() -> None:
    event = ProactiveEventMessage.model_validate(
        {
            "event_id": "booking-event-1",
            "source": "booking",
            "event_type": "APPOINTMENT_CANCELLED",
            "aggregate_id": "appointment-1",
            "organization_id": "org-1",
            "aggregate_version": 3,
            "payload": {"studentId": "student-1", "coachId": "coach-1"},
        }
    )

    specs = _candidate_specs(event)

    assert len(specs) == 1
    assert specs[0]["candidate_type"].value == "APPOINTMENT_FOLLOW_UP"
    assert specs[0]["subject_user_id"] == "student-1"


def test_decision_contract_rejects_free_form_fields_and_invalid_defer() -> None:
    with pytest.raises(ValueError):
        ProactiveAgentOutput.model_validate(
            {
                "decision": "DEFER",
                "reason_code": "TEST",
                "strategy": "WAIT",
                "confidence": 0.8,
                "defer_minutes": 0,
                "recipient_user_id": "attacker-selected-user",
            }
        )
