"""Replay one real booking Outbox fact through the Python consumer and PG worker.

The envelope is reconstructed from the real MySQL row; the delivery object is a
local stub, not RabbitMQ. No notification delivery worker or real model is run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import text

from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository
from app.proactive.events import ProactiveEventMessage
from app.proactive.rabbit_consumer import ProactiveRabbitConsumer
from app.proactive.repository import ProactiveEventRepository
from app.proactive.worker import ProactiveEventWorker


class Delivery:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acks = 0
        self.rejects = 0

    async def ack(self) -> None:
        self.acks += 1

    async def reject(self, *, requeue: bool) -> None:
        self.rejects += 1


# This probe is only valid for the two-tenant disposable fixture established by
# cross_service_booking; equal snapshots alone cannot prove an approved booking
# happened, and missing fields must never be interpreted as zero.
_EXPECTED_MYSQL_FACTS = {
    "contract_count": 1,
    "remaining_hours": 1,
    "appointments": 1,
    "active_appointments": 1,
    "audit_count": 1,
    "consumption_count": 1,
    "linked_audit_and_consumption": 1,
    "witness_contract_count": 1,
    "witness_remaining_hours": 5,
    "witness_appointments": 0,
    "global_contracts": 2,
    "global_remaining_hours": 6,
    "global_appointments": 1,
    "global_create_operations": 1,
    "global_reschedule_operations": 0,
    "global_cancel_operations": 0,
    "global_audits": 1,
    "global_consumptions": 1,
    "global_outbox": 1,
}


def _matches_approved_booking(snapshot: dict[str, Any]) -> bool:
    facts = snapshot.get("facts")
    return (
        snapshot.get("snapshot_complete") is True
        and snapshot.get("witness_observed") is True
        and isinstance(facts, dict)
        and all(
            type(facts.get(key)) is int and facts[key] == value
            for key, value in _EXPECTED_MYSQL_FACTS.items()
        )
        and facts.get("audit_actions") == ["CREATE_APPOINTMENT"]
    )


async def _pg_snapshot(database: Database, event_id: str) -> dict[str, Any]:
    async with database.engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        row = (
            (
                await connection.execute(
                    text("""
                    SELECT
                      (SELECT count(*) FROM agent_proactive_event_inbox) AS inbox_total,
                      (SELECT count(*) FROM agent_proactive_event_inbox
                       WHERE event_id = :event_id AND status = 'PROCESSED') AS inbox_processed,
                      (SELECT count(*) FROM agent_notification_outbox) AS outbox_total,
                      (SELECT count(*) FROM agent_notification_outbox
                       WHERE payload->>'event_id' = :event_id AND status = 'PENDING') AS pending,
                      (SELECT array_agg(subject_user_id ORDER BY subject_user_id)
                       FROM agent_notification_outbox WHERE payload->>'event_id' = :event_id) AS recipients
                """),
                    {"event_id": event_id},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


def verify_replay(
    *,
    baseline: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    first_acks: int,
    second_acks: int,
    first_rejects: int,
    second_rejects: int,
    worker_first: tuple[int, int, int],
    worker_second: tuple[int, int, int],
    mysql_before: dict[str, Any],
    mysql_after: dict[str, Any],
) -> None:
    """Fail on missing/repeated effects; the inspected snapshots are not a complete audit."""
    if (
        first_acks != 1
        or second_acks != 1
        or first_rejects != 0
        or second_rejects != 0
        or worker_first != (1, 1, 0)
        or worker_second != (0, 0, 0)
        or baseline.get("inbox_total") != 0
        or baseline.get("outbox_total") != 0
        or first
        != {
            "inbox_total": 1,
            "inbox_processed": 1,
            "outbox_total": 2,
            "pending": 2,
            "recipients": ["coach-1", "student-1"],
        }
        or second != first
        or not _matches_approved_booking(mysql_before)
        or not _matches_approved_booking(mysql_after)
        or mysql_before.get("facts") != mysql_after.get("facts")
        or any(
            type(snapshot.get(field)) is not int
            for snapshot in (baseline, first, second)
            for field in ("inbox_total", "inbox_processed", "outbox_total", "pending")
        )
    ):
        raise ValueError("DUPLICATE_EVENT_EFFECT_MISMATCH")


async def run_duplicate_event_probe(
    *,
    database: Database,
    event: ProactiveEventMessage,
    mysql_snapshot: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if (
        event.event_type != "APPOINTMENT_CREATED"
        or event.source != "booking"
        or event.organization_id != "org-1"
        or event.event_id != f"appointment-created:{event.aggregate_id}"
        or event.contract_version != 1
        or event.aggregate_version != 1
        or event.payload
        != {
            "appointmentId": event.aggregate_id,
            "studentId": "student-1",
            "coachId": "coach-1",
        }
    ):
        raise ValueError("INVALID_EVENT_FIXTURE")
    # The exact same bytes are delivered twice to the actual consumer handler.
    body = json.dumps(
        event.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
    ).encode()
    first_delivery, second_delivery = Delivery(body), Delivery(body)
    repository = ProactiveEventRepository()
    consumer = ProactiveRabbitConsumer(
        database,
        repository,
        url="amqp://not-used",
        exchange_name="local",
        queue_name="local",
        routing_key="local",
    )
    worker = ProactiveEventWorker(
        database,
        repository,
        NotificationOutboxRepository(),
        batch_size=10,
        worker_id="task-eval-duplicate",
    )
    before_mysql = await mysql_snapshot()
    baseline = await _pg_snapshot(database, event.event_id)
    await consumer._consume_one(cast(AbstractIncomingMessage, first_delivery))
    first_run = await worker.run_once()
    first = await _pg_snapshot(database, event.event_id)
    await consumer._consume_one(cast(AbstractIncomingMessage, second_delivery))
    second_run = await worker.run_once()
    second = await _pg_snapshot(database, event.event_id)
    after_mysql = await mysql_snapshot()
    one = (first_run.claimed, first_run.processed, first_run.retried)
    two = (second_run.claimed, second_run.processed, second_run.retried)
    verify_replay(
        baseline=baseline,
        first=first,
        second=second,
        first_acks=first_delivery.acks,
        second_acks=second_delivery.acks,
        first_rejects=first_delivery.rejects,
        second_rejects=second_delivery.rejects,
        worker_first=one,
        worker_second=two,
        mysql_before=before_mysql,
        mysql_after=after_mysql,
    )
    return {
        "case_id": "BOOK-030",
        "component_status": "OBSERVED",
        "task_status": "UNKNOWN",
        "event_source": "actual_mysql_booking_outbox_reconstructed_envelope",
        "duplicate_delivery": "same_bytes_twice_to_python_consumer_with_stub_message",
        "event_body_sha256": hashlib.sha256(body).hexdigest(),
        "delivery_acks": [first_delivery.acks, second_delivery.acks],
        "delivery_rejects": [first_delivery.rejects, second_delivery.rejects],
        "postgres_before": baseline,
        "postgres_after_first": first,
        "postgres_after_second": second,
        "mysql_before": before_mysql["facts"],
        "mysql_after": after_mysql["facts"],
        "first_worker": {"claimed": one[0], "processed": one[1], "retried": one[2]},
        "second_worker": {"claimed": two[0], "processed": two[1], "retried": two[2]},
        "inbox_rows": second["inbox_total"],
        "notification_outbox_rows": second["outbox_total"],
        "mysql_observed_facts_unchanged": True,
        "audit_complete": False,
        "limitations": [
            "The actual broker and Java publisher are not used; the envelope is reconstructed from the persisted booking Outbox row.",
            "The event consumer generates notification Outbox rows, not bookings or credit charges; no real notification is delivered.",
            "MySQL snapshots are not a complete database transaction audit; the scripted model does not test interpretation.",
            "The planning case and semantic evaluation remain unreviewed; this component observation is not an end-to-end task PASS.",
        ],
    }
