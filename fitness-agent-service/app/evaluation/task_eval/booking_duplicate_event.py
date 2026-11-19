"""Replay one real booking Outbox fact through the Python consumer and PG worker.

Delivery defaults to a local stub with a reconstructed envelope. The explicit
broker mode consumes the Java publisher's actual bytes from a verified disposable
local RabbitMQ. Notifications reach only the disposable PostgreSQL in-app inbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any, cast

from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import text

from app.infrastructure.database import Database
from app.notifications.channels import InAppNotificationChannelAdapter
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.worker import NotificationOutboxWorker
from app.proactive.events import ProactiveEventMessage
from app.proactive.rabbit_consumer import ProactiveRabbitConsumer
from app.proactive.repository import ProactiveEventRepository
from app.proactive.worker import ProactiveEventWorker

from .booking_rabbit_delivery import BrokerFixture, JavaPublisherDelivery


class Delivery:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acks = 0
        self.rejects = 0

    async def ack(self) -> None:
        self.acks += 1

    async def reject(self, *, requeue: bool) -> None:
        self.rejects += 1


class _TrackedBrokerDelivery(Delivery):
    """Record ACK/reject only after the real broker method succeeds."""

    def __init__(self, message: AbstractIncomingMessage) -> None:
        super().__init__(message.body)
        self.message = message

    async def ack(self) -> None:
        await self.message.ack()
        self.acks += 1

    async def reject(self, *, requeue: bool) -> None:
        await self.message.reject(requeue=requeue)
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


def _confirmed_java_publisher(status: dict[str, Any]) -> bool:
    """Require actual boolean status fields and an integer attempt count."""
    return (
        status.get("status") == "PUBLISHED"
        and status.get("published") is True
        and status.get("unclaimed") is True
        and status.get("error_free") is True
        and type(status.get("attempt_count")) is int
        and status["attempt_count"] == 0
        and set(status) == {"status", "published", "unclaimed", "error_free", "attempt_count"}
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


async def _delivery_snapshot(database: Database, event: ProactiveEventMessage) -> dict[str, Any]:
    """Read the whole disposable notification database, not just matching rows."""
    async with database.engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        row = (
            (
                await connection.execute(
                    text("""
                    SELECT
                      (SELECT count(*) FROM agent_in_app_notifications) AS inbox_total,
                      (SELECT count(*) FROM agent_notification_delivery_attempts) AS attempts_total,
                      (SELECT count(*) FROM agent_notification_outbox
                       WHERE payload->>'event_id' = :event_id AND status = 'PUBLISHED') AS published,
                      (SELECT array_agg(subject_user_id ORDER BY subject_user_id)
                       FROM agent_in_app_notifications) AS recipients,
                      (SELECT count(*) FROM agent_notification_outbox o
                       JOIN agent_in_app_notifications n ON n.dedupe_key = o.dedupe_key
                       JOIN agent_notification_delivery_attempts a ON a.outbox_id = o.id
                       WHERE o.payload->>'event_id' = :event_id
                         AND o.notification_type = 'APPOINTMENT_CREATED'
                         AND o.organization_id = 'org-1' AND o.aggregate_id = :aggregate_id
                         AND o.status = 'PUBLISHED'
                         AND n.notification_type = o.notification_type
                         AND n.subject_user_id = o.subject_user_id
                         AND n.organization_id = o.organization_id
                         AND n.aggregate_type = o.aggregate_type AND n.aggregate_id = o.aggregate_id
                         AND n.status = 'UNREAD' AND n.template_version = 1
                         AND length(trim(n.title)) > 0 AND length(trim(n.body)) > 0
                         AND a.channel = 'IN_APP' AND a.attempt_no = 1
                         AND a.status = 'SUCCEEDED' AND a.provider_message_id = n.id
                         AND a.finished_at IS NOT NULL) AS matched_delivery
                """),
                    {"event_id": event.event_id, "aggregate_id": event.aggregate_id},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


def verify_delivery(
    *,
    baseline: dict[str, Any],
    delivered: dict[str, Any],
    replayed: dict[str, Any],
    first_run: tuple[int, int, int, int, int],
    second_run: tuple[int, int, int, int, int],
    replay_ack: int,
    replay_reject: int,
    replay_worker: tuple[int, int, int],
    replay_event_snapshot: dict[str, Any],
) -> None:
    """Require exact recipients, successful durable receipts and no replay effects."""
    empty = {
        "inbox_total": 0,
        "attempts_total": 0,
        "published": 0,
        "recipients": None,
        "matched_delivery": 0,
    }
    expected = {
        "inbox_total": 2,
        "attempts_total": 2,
        "published": 2,
        "recipients": ["coach-1", "student-1"],
        "matched_delivery": 2,
    }
    counts = ("inbox_total", "attempts_total", "published", "matched_delivery")
    if (
        baseline != empty
        or delivered != expected
        or replayed != delivered
        or first_run != (2, 2, 0, 0, 0)
        or second_run != (0, 0, 0, 0, 0)
        or replay_ack != 1
        or replay_reject != 0
        or replay_worker != (0, 0, 0)
        or replay_event_snapshot
        != {
            "inbox_total": 1,
            "inbox_processed": 1,
            "outbox_total": 2,
            "pending": 0,
            "recipients": ["coach-1", "student-1"],
        }
        or any(
            type(replay_event_snapshot.get(field)) is not int
            for field in ("inbox_total", "inbox_processed", "outbox_total", "pending")
        )
        or any(
            type(s.get(field)) is not int
            for s in (baseline, delivered, replayed)
            for field in counts
        )
    ):
        raise ValueError("NOTIFICATION_DELIVERY_MISMATCH")


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
    broker: BrokerFixture | None = None,
    publisher_status: Callable[[], Awaitable[dict[str, Any]]] | None = None,
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
    if (broker is None) != (publisher_status is None):
        raise ValueError("INVALID_PUBLISHER_OBSERVER")
    # The exact same bytes are delivered twice to the actual consumer handler.
    body = json.dumps(
        event.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
    ).encode()
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
    delivery_baseline = await _delivery_snapshot(database, event)
    if delivery_baseline != {
        "inbox_total": 0,
        "attempts_total": 0,
        "published": 0,
        "recipients": None,
        "matched_delivery": 0,
    }:
        raise ValueError("NOTIFICATION_BASELINE_NOT_EMPTY")
    async with AsyncExitStack() as stack:
        transport = (
            await stack.enter_async_context(JavaPublisherDelivery(broker, body)) if broker else None
        )
        first_delivery = (
            _TrackedBrokerDelivery(await transport.receive_java()) if transport else Delivery(body)
        )
        publisher_observation: dict[str, Any] | None = None
        if transport:
            actual = ProactiveEventMessage.from_json(first_delivery.body)
            if (
                len(first_delivery.body) > 16384
                or actual.occurred_at is None
                or actual.model_dump(exclude={"occurred_at"})
                != event.model_dump(exclude={"occurred_at"})
                or publisher_status is None
            ):
                raise ValueError("JAVA_PUBLISHED_EVENT_MISMATCH")
            body = first_delivery.body
            for _ in range(25):
                publisher_observation = await publisher_status()
                if _confirmed_java_publisher(publisher_observation):
                    break
                await asyncio.sleep(0.2)
            if not _confirmed_java_publisher(publisher_observation or {}):
                raise ValueError("JAVA_PUBLISHER_NOT_CONFIRMED")
        await consumer._consume_one(cast(AbstractIncomingMessage, first_delivery))
        first_run = await worker.run_once()
        first = await _pg_snapshot(database, event.event_id)
        second_delivery = (
            _TrackedBrokerDelivery(await transport.receive()) if transport else Delivery(body)
        )
        await consumer._consume_one(cast(AbstractIncomingMessage, second_delivery))
        second_run = await worker.run_once()
        second = await _pg_snapshot(database, event.event_id)
        # Do not publish notifications unless the first two deliveries already
        # match this exact fixture and the independent MySQL observation.
        verify_replay(
            baseline=baseline,
            first=first,
            second=second,
            first_acks=first_delivery.acks,
            second_acks=second_delivery.acks,
            first_rejects=first_delivery.rejects,
            second_rejects=second_delivery.rejects,
            worker_first=(first_run.claimed, first_run.processed, first_run.retried),
            worker_second=(second_run.claimed, second_run.processed, second_run.retried),
            mysql_before=before_mysql,
            mysql_after=await mysql_snapshot(),
        )
        # Explicitly allow only the in-app adapter: this worker has no SMS,
        # push, webhook, or other external egress even if defaults change.
        notification_repository = NotificationOutboxRepository()
        notification_worker = NotificationOutboxWorker(
            database,
            notification_repository,
            batch_size=10,
            worker_id="task-eval-in-app-only",
            channel_adapters={"IN_APP": InAppNotificationChannelAdapter(notification_repository)},
        )
        published_run = await notification_worker.run_once()
        delivered = await _delivery_snapshot(database, event)
        third_delivery = (
            _TrackedBrokerDelivery(await transport.receive()) if transport else Delivery(body)
        )
        await consumer._consume_one(cast(AbstractIncomingMessage, third_delivery))
        third_run = await worker.run_once()
        republished_run = await notification_worker.run_once()
        replayed = await _delivery_snapshot(database, event)
        replay_event_snapshot = await _pg_snapshot(database, event.event_id)
        if transport and not await transport.empty():
            raise ValueError("UNEXPECTED_BROKER_MESSAGE")
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
    published = (
        published_run.claimed,
        published_run.published,
        published_run.retried,
        published_run.deferred,
        published_run.suppressed,
    )
    republished = (
        republished_run.claimed,
        republished_run.published,
        republished_run.retried,
        republished_run.deferred,
        republished_run.suppressed,
    )
    verify_delivery(
        baseline=delivery_baseline,
        delivered=delivered,
        replayed=replayed,
        first_run=published,
        second_run=republished,
        replay_ack=third_delivery.acks,
        replay_reject=third_delivery.rejects,
        replay_worker=(third_run.claimed, third_run.processed, third_run.retried),
        replay_event_snapshot=replay_event_snapshot,
    )
    return {
        "case_id": "BOOK-030",
        "component_status": "OBSERVED",
        "task_status": "UNKNOWN",
        "event_source": (
            "java_booking_outbox_publisher_via_disposable_rabbitmq"
            if broker
            else "actual_mysql_booking_outbox_reconstructed_envelope"
        ),
        "duplicate_delivery": (
            "same_bytes_twice_via_disposable_rabbitmq_to_python_consumer"
            if broker
            else "same_bytes_twice_to_python_consumer_with_stub_message"
        ),
        "event_body_sha256": hashlib.sha256(body).hexdigest(),
        "delivery_acks": [first_delivery.acks, second_delivery.acks],
        "delivery_rejects": [first_delivery.rejects, second_delivery.rejects],
        "java_publisher_outbox": publisher_observation,
        "postgres_before": baseline,
        "postgres_after_first": first,
        "postgres_after_second": second,
        "mysql_before": before_mysql["facts"],
        "mysql_after": after_mysql["facts"],
        "first_worker": {"claimed": one[0], "processed": one[1], "retried": one[2]},
        "second_worker": {"claimed": two[0], "processed": two[1], "retried": two[2]},
        "inbox_rows": second["inbox_total"],
        "notification_outbox_rows": second["outbox_total"],
        "notification_delivery": {
            "channel": "IN_APP",
            "scope": "disposable_postgres_only_no_external_notification_adapter",
            "before": delivery_baseline,
            "after_delivery": delivered,
            "after_post_delivery_replay": replayed,
            "event_after_post_delivery_replay": replay_event_snapshot,
            "post_delivery_replay_acks": third_delivery.acks,
            "post_delivery_replay_rejects": third_delivery.rejects,
        },
        "mysql_observed_facts_unchanged": True,
        "audit_complete": False,
        "limitations": [
            (
                "The first event is from the Java publisher; subsequent exact-byte replays are sent by the test harness."
                if broker
                else "The actual broker and Java publisher are not used; the envelope is reconstructed from the persisted booking Outbox row."
            ),
            "Notifications are delivered only to the disposable PostgreSQL in-app inbox; no external provider or actual user receives them.",
            "MySQL snapshots are not a complete database transaction audit; the scripted model does not test interpretation.",
            "The planning case and semantic evaluation remain unreviewed; this component observation is not an end-to-end task PASS.",
        ],
    }
