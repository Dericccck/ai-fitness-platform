"""Resolve BOOK-030's fixed synthetic proposal fixture, without asserting execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent.fitness_tools import BookingCreateToolInput

from . import booking_local
from .booking_contracts import matches_frozen_case

_FIXTURE = Path(__file__).resolve().parents[3] / "evals/task_eval/dev_fixtures/book-030-v1.json"
_SHA256 = "cfae524e09ecd49f48eadaa86fa285ef01f1f5da5716e9efc68e94ea4d93df4a"


def load(case: Any) -> dict[str, Any]:
    """Bind draft parameters to original test case and the disposable booking identity."""
    if (
        case.case_id != "BOOK-030"
        or case.split != "test"
        or case.review.status != "draft"
        or not matches_frozen_case(case)
    ):
        raise ValueError("TEST_CASE_CHANGED")
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _SHA256:
        raise ValueError("TEST_FIXTURE_CHANGED")
    fixture = json.loads(raw)
    payload = fixture["payload"]
    BookingCreateToolInput.model_validate(payload)
    start = datetime.fromisoformat(payload["start_time"])
    end = datetime.fromisoformat(payload["end_time"])
    expected_message = (
        f"请预约 {payload['organization_id']} 的 {payload['student_id']} 使用 "
        f"{payload['contract_id']}，{payload['coach_id']} 的 {payload['course_id']}，"
        f"{start:%Y-%m-%d %H:%M} 至 {end:%H:%M}（东八区）。"
    )
    if (
        fixture["version"] != "1.0"
        or fixture["status"] != "synthetic_test_draft"
        or fixture["case_id"] != case.case_id
        or fixture["message_ref"] != case.steps[0].message_ref
        or fixture["identity"]
        != {"organization_id": payload["organization_id"], "student_id": payload["student_id"]}
        or payload["student_id"] != booking_local.IDENTITY.subject
        or frozenset({payload["organization_id"]}) != booking_local.IDENTITY.organization_ids
        or start.utcoffset() != timedelta(hours=8)
        or end.utcoffset() != timedelta(hours=8)
        or start >= end
        or start.date() != end.date()
        or fixture["message"] != expected_message
    ):
        raise ValueError("TEST_FIXTURE_INCONSISTENT")
    return {"message": expected_message, "payload": payload, "sha256": _SHA256}
