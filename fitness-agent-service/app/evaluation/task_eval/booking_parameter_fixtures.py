"""Case-bound synthetic booking inputs for an unreviewed scoring proposal.

These records are supplemental inputs, not executions, observed state, or a
substitute for verifying course availability and contract eligibility.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .capacity_draft import load as load_capacity
from .dataset import Dataset

FIXTURE_SHA256 = "4bd49b12cbf0241c0d70eb1da9c76dd568527a867a5ed3c21333c14d18f2c873"
_SOURCE_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "evals/task_eval/dev_fixtures/booking-positive-and-fault-proposal-v1.json"
)
POSITIVE = frozenset(
    {"BOOK-001", "BOOK-002", "BOOK-003", "BOOK-004", "BOOK-005", "BOOK-007", "BOOK-010"}
)
FAULT = frozenset({"BOOK-028", "BOOK-029", "BOOK-031"})
PARTIAL = frozenset({"BOOK-006", "BOOK-009"})


def load_contracts(dataset: Dataset) -> dict[str, dict[str, Any]]:
    if dataset.sha256 != _SOURCE_SHA256:
        raise ValueError("BOOKING_PROPOSAL_SOURCE_CHANGED")
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError("BOOKING_PROPOSAL_FIXTURE_CHANGED")
    doc = json.loads(raw)
    if (
        doc.get("schema_version") != "1.0"
        or doc.get("status") != "draft_unreviewed_not_execution_evidence"
    ):
        raise ValueError("BOOKING_PROPOSAL_INVALID")
    cases = {case.case_id: case for case in dataset.cases}
    result: dict[str, dict[str, Any]] = {}
    for row in doc["cases"]:
        cid = row["case_id"]
        if cid not in POSITIVE | FAULT | PARTIAL or cid in result or cid not in cases:
            raise ValueError("BOOKING_PROPOSAL_CASE_INVALID")
        case = cases[cid]
        payload = row["payload"]
        slug = cid.lower()
        identity = {
            key: f"synthetic-{slug}-{suffix}"
            for key, suffix in (
                ("organization_id", "org"),
                ("student_id", "student"),
                ("contract_id", "contract"),
                ("coach_id", "coach"),
                ("course_id", "course"),
            )
        }
        if cid == "BOOK-009":
            capacity = load_capacity(case)
            identity["course_id"] = capacity["course_id"]
            if row.get("capacity_fixture_sha256") != capacity["fixture_sha256"]:
                raise ValueError("BOOKING_CAPACITY_FIXTURE_CHANGED")
        if cid == "BOOK-006":
            store = f"synthetic-{slug}-store"
            if row.get("store_id") != store or store not in row["message"]:
                raise ValueError("BOOKING_STORE_PROPOSAL_INVALID")
        if (
            row["status"] != "draft_unreviewed"
            or case.split != row["split"]
            or case.review.status != "draft"
            or case.capability_status != "planned"
            or any(payload.get(key) != value for key, value in identity.items())
            or any(value not in row["message"] for value in identity.values())
            or row["synthetic_assumptions"]
            != {
                "contract_status": "active",
                "contract_course_match": True,
                "remaining_credits_before": 3 if cid == "BOOK-009" else 2,
                "credits_per_booking": 1,
                "timeslot_available": True,
            }
        ):
            raise ValueError("BOOKING_PROPOSAL_CASE_INVALID")
        result[cid] = row
    if result.keys() != POSITIVE | FAULT | PARTIAL:
        raise ValueError("BOOKING_PROPOSAL_CASES_MISSING")
    return result
