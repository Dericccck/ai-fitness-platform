"""Frozen synthetic development fixture for the BOOK-018 planning reference.

This resolves a developer-authored example; it is not human review, a model
interpretation test, a production fixture, or a release-eligible test case.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import booking_local
from .booking_contracts import matches_frozen_case

_FIXTURE = Path(__file__).resolve().parents[3] / "evals/task_eval/dev_fixtures/book-018-v1.json"
_SHA256 = "2f2ddb98054ba5fd5af4fd8f2771b5b1bbdf9c14086b022bde7fdb03137d3652"


def load_book_018(case: Any) -> dict[str, Any]:
    if (
        case.case_id != "BOOK-018"
        or case.split != "dev"
        or case.review.status != "draft"
        or not matches_frozen_case(case)
    ):
        raise ValueError("DEV_CASE_CHANGED")
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _SHA256:
        raise ValueError("DEV_FIXTURE_CHANGED")
    fixture = json.loads(raw)
    payload = fixture["payload"]
    start, end = (
        datetime.fromisoformat(payload["start_time"]),
        datetime.fromisoformat(payload["end_time"]),
    )
    if (
        fixture["version"] != "1.0"
        or fixture["status"] != "synthetic_dev_draft"
        or fixture["case_id"] != case.case_id
        or fixture["message_ref"] != case.steps[0].message_ref
        or fixture["identity"]
        != {"organization_id": payload["organization_id"], "student_id": payload["student_id"]}
        or payload["student_id"] != booking_local.IDENTITY.subject
        or frozenset({payload["organization_id"]}) != booking_local.IDENTITY.organization_ids
        or start.utcoffset() != timedelta(hours=8)
        or end.utcoffset() != timedelta(hours=8)
        or start <= datetime.now(UTC)
        or start >= end
        or start.date() != end.date()
    ):
        raise ValueError("DEV_FIXTURE_INCONSISTENT")
    message = (
        f"请预约 {payload['organization_id']} 的 {payload['student_id']} 使用 "
        f"{payload['contract_id']}，{payload['coach_id']} 的 {payload['course_id']}，"
        f"{start:%Y-%m-%d %H:%M} 至 {end:%H:%M}（东八区）。"
    )
    if fixture["message"] != message:
        raise ValueError("DEV_FIXTURE_INCONSISTENT")
    return {"message": message, "payload": payload, "sha256": _SHA256}
