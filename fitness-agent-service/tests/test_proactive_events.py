import json

import pytest

from app.proactive.events import (
    ProactiveEventContractError,
    ProactiveEventMessage,
    notification_targets,
)


def appointment_event() -> ProactiveEventMessage:
    return ProactiveEventMessage.from_json(
        json.dumps(
            {
                "eventId": "appointment-created:appointment-1",
                "source": "booking",
                "eventType": "APPOINTMENT_CREATED",
                "aggregateId": "appointment-1",
                "organizationId": "org-1",
                "payload": {"studentId": "student-1", "coachId": "coach-1"},
            }
        ).encode()
    )


def training_published_event() -> ProactiveEventMessage:
    return ProactiveEventMessage.from_json(
        json.dumps(
            {
                "eventId": "training-plan-published:plan-1:request-1",
                "source": "training",
                "eventType": "TRAINING_PLAN_PUBLISHED",
                "aggregateId": "plan-1",
                "organizationId": "org-1",
                "payload": {"planId": "plan-1", "studentId": "student-1", "coachId": "coach-1"},
            }
        ).encode()
    )


def test_booking_event_accepts_java_camel_case_envelope_and_routes_two_targets() -> None:
    event = appointment_event()

    assert event.event_id == "appointment-created:appointment-1"
    assert [(target.user_id, target.role) for target in notification_targets(event)] == [
        ("student-1", "STUDENT"),
        ("coach-1", "COACH"),
    ]


def test_training_event_accepts_training_source_and_routes_student() -> None:
    event = training_published_event()

    assert event.source == "training"
    assert notification_targets(event)[0].user_id == "student-1"
    assert notification_targets(event)[0].role == "STUDENT"


def test_same_student_and_coach_is_not_notified_twice() -> None:
    event = appointment_event().model_copy(
        update={"payload": {"studentId": "same-user", "coachId": "same-user"}}
    )

    assert notification_targets(event) == (
        notification_targets(appointment_event())[0].__class__("same-user", "STUDENT"),
    )


def test_unknown_event_type_is_rejected_before_inbox_persistence() -> None:
    with pytest.raises(ProactiveEventContractError, match="unsupported proactive event type"):
        ProactiveEventMessage.from_json(
            json.dumps(
                {
                    "eventId": "event-1",
                    "source": "booking",
                    "eventType": "UNSUPPORTED",
                    "aggregateId": "aggregate-1",
                    "organizationId": "org-1",
                    "payload": {},
                }
            ).encode()
        )


def test_unknown_event_source_is_rejected_before_inbox_persistence() -> None:
    with pytest.raises(ProactiveEventContractError, match="unsupported proactive event source"):
        ProactiveEventMessage.from_json(
            json.dumps(
                {
                    "eventId": "event-1",
                    "source": "unknown-service",
                    "eventType": "TRAINING_PLAN_PUBLISHED",
                    "aggregateId": "plan-1",
                    "organizationId": "org-1",
                    "payload": {"studentId": "student-1"},
                }
            ).encode()
        )


def test_missing_recipient_is_rejected() -> None:
    event = appointment_event().model_copy(update={"payload": {"studentId": "student-1"}})

    with pytest.raises(ProactiveEventContractError, match="recipient ID is missing"):
        notification_targets(event)
