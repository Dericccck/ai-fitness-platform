"""Synthetic, deterministic evaluation of the actual notification preference policy.

Only the preference decision is exercised; this does not dispatch notifications or
validate SQL, worker locking, expiry, consent beyond the enabled flag, or an Agent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.notifications.outbox import NotificationOutboxRecord
from app.notifications.preferences import (
    NotificationPreferenceRecord,
    NotificationPreferenceRepository,
)


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class _Connection:
    """Controlled read-only fixture; rejects unexpected queries and scope drift."""

    def __init__(self, recent: datetime | None) -> None:
        self.recent = recent
        self.reads: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        if "FROM agent_in_app_notifications" not in str(statement):
            raise ValueError("unexpected query")
        self.reads.append(dict(params))
        return _Result({"created_at": self.recent} if self.recent else None)


class _Repository(NotificationPreferenceRepository):
    def __init__(self, preference: NotificationPreferenceRecord) -> None:
        super().__init__()
        self.preference = preference
        self.preference_reads: list[dict[str, str]] = []

    async def get(
        self, connection: Any, *, subject_user_id: str, organization_id: str, notification_type: str
    ) -> NotificationPreferenceRecord:
        scope = {
            "subject_user_id": subject_user_id,
            "organization_id": organization_id,
            "notification_type": notification_type,
        }
        self.preference_reads.append(scope)
        if scope != {key: getattr(self.preference, key) for key in scope}:
            raise ValueError("unexpected preference scope")
        return self.preference


async def evaluate_notification_policy() -> dict[str, Any]:
    """Evaluate policy cases against literal expectations, returning observed evidence."""
    now = datetime(2026, 8, 14, 14, 30, tzinfo=UTC)
    scope = {
        "subject_user_id": "synthetic-user",
        "organization_id": "synthetic-org",
        "notification_type": "APPOINTMENT_FOLLOW_UP",
    }
    preference = NotificationPreferenceRecord(
        **scope,
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
        subject_user_id=scope["subject_user_id"],
        organization_id=scope["organization_id"],
        notification_type=scope["notification_type"],
        aggregate_type="APPOINTMENT",
        aggregate_id="synthetic-appointment",
        dedupe_key="synthetic",
        payload={},
        status="PENDING",
        attempt_count=0,
        available_at=now,
        locked_by=None,
        locked_at=None,
        last_error_code=None,
        published_at=None,
        created_at=now,
        updated_at=now,
    )
    cases = [
        ("disabled", replace(preference, enabled=False), None, "SUPPRESS", "USER_DISABLED", None),
        (
            "quiet_hours",
            replace(preference, quiet_start=time(22), quiet_end=time(8)),
            None,
            "DEFER",
            "QUIET_HOURS",
            datetime(2026, 8, 15, 0, tzinfo=UTC),
        ),
        (
            "frequency_limit",
            replace(preference, minimum_interval_seconds=3600),
            now - timedelta(minutes=30),
            "SUPPRESS",
            "FREQUENCY_LIMIT",
            None,
        ),
        (
            "frequency_boundary",
            replace(preference, minimum_interval_seconds=3600),
            now - timedelta(hours=1),
            "ALLOW",
            None,
            None,
        ),
        ("enabled", preference, None, "ALLOW", None, None),
    ]
    results: list[dict[str, Any]] = []
    for case_id, pref, recent, action, reason, available_at in cases:
        repo, connection = _Repository(pref), _Connection(recent)
        try:
            decision = await repo.evaluate(connection, record=record, now=now)
        except Exception:  # noqa: BLE001 - keep the report complete without leaking exception data
            results.append(
                {
                    "case_id": case_id,
                    "status": "ERROR",
                    "reason": "POLICY_EXCEPTION",
                    "preference_reads": repo.preference_reads,
                    "notification_reads": connection.reads,
                }
            )
            continue
        actual = {
            "action": decision.action,
            "reason": decision.reason,
            "available_at": decision.available_at.isoformat() if decision.available_at else None,
        }
        expected = {
            "action": action,
            "reason": reason,
            "available_at": available_at.isoformat() if available_at else None,
        }
        scope_correct = repo.preference_reads == [scope] and all(
            read == scope for read in connection.reads
        )
        results.append(
            {
                "case_id": case_id,
                "status": "PASS" if actual == expected and scope_correct else "FAIL",
                "expected": expected,
                "observed": actual,
                "preference_reads": repo.preference_reads,
                "notification_reads": connection.reads,
            }
        )
    return {
        "scope": "notification_preference_policy_component_only",
        "source": "synthetic",
        "limitations": [
            "No delivery, database, worker concurrency, expiry, or end-to-end Agent tested."
        ],
        "cases": results,
        "passed": all(result["status"] == "PASS" for result in results),
    }


def main() -> int:
    report = asyncio.run(evaluate_notification_policy())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
