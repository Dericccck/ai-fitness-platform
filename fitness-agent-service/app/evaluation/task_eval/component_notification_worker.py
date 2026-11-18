"""Exercise the real notification worker and policy with isolated in-memory boundaries.

The fake boundaries do not establish SQL transactions, real delivery or event eligibility.
Each task case retains UNKNOWN while component observations are reported separately.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast

from app.infrastructure.database import Database
from app.notifications.channels import NotificationDeliveryReceipt
from app.notifications.outbox import NotificationOutboxRecord, NotificationOutboxRepository
from app.notifications.preferences import (
    NotificationPolicyDecision,
    NotificationPreferenceRecord,
    NotificationPreferenceRepository,
)
from app.notifications.templates import NotificationTemplateRecord, NotificationTemplateRepository
from app.notifications.worker import NotificationOutboxWorker

from .component_notification import _Connection, _Repository


class _WorkerConnection(_Connection):
    async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        if "pg_advisory_xact_lock" in str(statement):
            return None  # Only records orchestration; no concurrency or lock guarantee.
        return await super().execute(statement, params)


class _Database:
    def __init__(self, connection: _WorkerConnection) -> None:
        self.engine = self
        self.connection = connection

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_WorkerConnection]:
        yield self.connection


class _FixedClockPreferences(_Repository):
    async def evaluate(
        self, connection: Any, *, record: NotificationOutboxRecord, **kwargs: Any
    ) -> NotificationPolicyDecision:
        return await NotificationPreferenceRepository.evaluate(
            self, connection, record=record, now=_NOW
        )


class _Outbox:
    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale
        self.events: list[tuple[str, str | None]] = []

    async def is_stale_before_delivery(self, connection: Any, *, record: Any) -> bool:
        return self.stale

    async def mark_suppressed(self, connection: Any, **kwargs: Any) -> bool:
        self.events.append(("suppressed", kwargs["reason"]))
        return True

    async def mark_deferred(self, connection: Any, **kwargs: Any) -> bool:
        self.events.append(("deferred", kwargs["reason"]))
        return True

    async def start_delivery_attempt(self, connection: Any, **kwargs: Any) -> None:
        self.events.append(("attempt_started", None))

    async def finish_delivery_attempt(self, connection: Any, **kwargs: Any) -> bool:
        self.events.append(("attempt_finished", kwargs["status"]))
        return True

    async def mark_published(self, connection: Any, **kwargs: Any) -> bool:
        self.events.append(("published", None))
        return True

    async def mark_retry(self, connection: Any, **kwargs: Any) -> bool:
        self.events.append(("retried", kwargs["error_code"]))
        return True


class _Templates:
    async def get_published(self, connection: Any, **kwargs: Any) -> NotificationTemplateRecord:
        return NotificationTemplateRecord(
            template_key="APPOINTMENT_FOLLOW_UP",
            channel="IN_APP",
            version=1,
            status="PUBLISHED",
            title_template="提醒",
            body_template="测试",
            variables=(),
            created_by="fixture",
            approved_by="fixture",
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )


class _Channel:
    channel = "IN_APP"

    def __init__(self) -> None:
        self.deliveries: list[str] = []

    async def deliver(self, connection: Any, request: Any) -> NotificationDeliveryReceipt:
        self.deliveries.append(request.record.id)
        return NotificationDeliveryReceipt(channel="IN_APP", provider_message_id="fixture-receipt")


_NOW = datetime(2026, 8, 14, 14, 30, tzinfo=UTC)
_SCOPE = {
    "subject_user_id": "synthetic-user",
    "organization_id": "synthetic-org",
    "notification_type": "APPOINTMENT_FOLLOW_UP",
}


async def evaluate_notification_worker() -> dict[str, Any]:
    preference = NotificationPreferenceRecord(
        **_SCOPE,
        enabled=True,
        quiet_start=None,
        quiet_end=None,
        timezone="Asia/Shanghai",
        minimum_interval_seconds=0,
        created_at=None,
        updated_at=None,
    )
    record = NotificationOutboxRecord(
        id="synthetic-outbox",
        subject_user_id=_SCOPE["subject_user_id"],
        organization_id=_SCOPE["organization_id"],
        notification_type=_SCOPE["notification_type"],
        aggregate_type="APPOINTMENT",
        aggregate_id="synthetic-appointment",
        dedupe_key="synthetic",
        payload={},
        status="PENDING",
        attempt_count=0,
        available_at=_NOW,
        locked_by=None,
        locked_at=None,
        last_error_code=None,
        published_at=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    cases = [
        (
            "OPER-009",
            replace(preference, enabled=False),
            None,
            False,
            "suppressed",
            0,
            "USER_DISABLED",
        ),
        (
            "OPER-010",
            replace(preference, quiet_start=time(22), quiet_end=time(8)),
            None,
            False,
            "deferred",
            0,
            "QUIET_HOURS",
        ),
        (
            "OPER-011",
            replace(preference, minimum_interval_seconds=3600),
            _NOW - timedelta(minutes=30),
            False,
            "suppressed",
            0,
            "FREQUENCY_LIMIT",
        ),
        ("OPER-012", preference, None, False, "published", 1, None),
        # This only checks how the worker reacts to a stale signal supplied by a fake repository.
        ("OPER-013", preference, None, True, "suppressed", 0, "STALE_BEFORE_DELIVERY"),
    ]
    results = []
    for case_id, pref, recent, stale, outcome, delivery_count, reason in cases:
        connection, outbox, channel = _WorkerConnection(recent), _Outbox(stale=stale), _Channel()
        policy = _FixedClockPreferences(pref)
        worker = NotificationOutboxWorker(
            cast(Database, _Database(connection)),
            cast(NotificationOutboxRepository, outbox),
            preferences=policy,
            channel_adapters={"IN_APP": channel},
            worker_id="fixture-worker",
        )
        worker.templates = cast(NotificationTemplateRepository, _Templates())
        try:
            observed = await worker._publish_one(record)
            events = outbox.events
            # Check side effects as well as return value; an accidental delivery on suppress/defer fails.
            expected_events = (
                [("attempt_started", None), ("attempt_finished", "SUCCEEDED"), ("published", None)]
                if outcome == "published"
                else [(outcome, reason)]
            )
            valid = (
                observed == outcome
                and len(channel.deliveries) == delivery_count
                and events == expected_events
            )
            results.append(
                {
                    "case_id": case_id,
                    "component_status": "PASS" if valid else "FAIL",
                    "task_status": "UNKNOWN",
                    "reason": "FAKE_DATABASE_AND_CHANNEL",
                    "expected": {
                        "outcome": outcome,
                        "delivery_count": delivery_count,
                        "events": expected_events,
                    },
                    "observed": {
                        "outcome": observed,
                        "delivery_count": len(channel.deliveries),
                        "events": events,
                    },
                }
            )
        except Exception:  # noqa: BLE001 - report safe structured failure, do not expose fixture details
            results.append(
                {
                    "case_id": case_id,
                    "component_status": "ERROR",
                    "task_status": "UNKNOWN",
                    "reason": "WORKER_EXCEPTION",
                }
            )
    return {
        "scope": "notification_worker_with_synthetic_boundaries",
        "source": "synthetic",
        "limitations": [
            "No real database transactions, lock, deduplication, candidate eligibility, consent or external delivery tested.",
            "Stale decision is supplied by the fixture; task-level results require independent evidence.",
        ],
        "cases": results,
        "passed": all(r["component_status"] == "PASS" for r in results),
    }


def main() -> int:
    report = asyncio.run(evaluate_notification_worker())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
