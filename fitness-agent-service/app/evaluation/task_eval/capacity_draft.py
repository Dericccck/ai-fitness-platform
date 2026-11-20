"""Case-bound synthetic capacity example; it does not invoke the product or an Agent.

The original booking service exposes no group-class capacity contract. These
examples make proposed test data and expected boundary behavior reproducible;
passing them cannot establish any property of that service.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "evals/task_eval/dev_fixtures/book-capacity-draft-v1.json"
)
_SHA256 = "b758da79a9307ff704f3487beea523a7827d9788a7bc9ccb6fb4ab2a295be3e3"
_CASE_HASHES = {
    "BOOK-009": "9ad72a8e1fa8549240aeb841f45c4b915a524727e227c48c1599380ab348745b",
    "BOOK-022": "9df388063c79c7c4af599eaf5d5f6ef5cda22cbb1a21f0e11b31b5967a8bc625",
}


@dataclass(frozen=True)
class Snapshot:
    occupied: int
    credits: int
    bookings: int


def load(case: Any) -> dict[str, Any]:
    expected = _CASE_HASHES.get(case.case_id)
    digest = hashlib.sha256(
        json.dumps(
            case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if expected is None or digest != expected:
        raise ValueError("DRAFT_CASE_CHANGED")
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _SHA256:
        raise ValueError("DRAFT_FIXTURE_CHANGED")
    fixture = json.loads(raw)
    item = fixture["cases"][case.case_id]
    if (
        fixture["version"] != "1.0"
        or fixture["status"] != "synthetic_draft_not_product"
        or item["case_sha256"] != expected
        or type(item["capacity"]) is not int
        or type(item["occupied"]) is not int
        or type(item["credits"]) is not int
        or item["capacity"] <= 0
        or not 0 <= item["occupied"] <= item["capacity"]
        or item["credits"] <= 0
        or not isinstance(item["course_id"], str)
        or not item["course_id"].startswith("synthetic-")
        or type(item["confirmation_required"]) is not bool
        or (
            case.case_id == "BOOK-009"
            and (item["occupied"] != item["capacity"] - 1 or not item["confirmation_required"])
        )
        or (
            case.case_id == "BOOK-022"
            and (item["occupied"] != item["capacity"] or item["confirmation_required"])
        )
    ):
        raise ValueError("DRAFT_FIXTURE_INCONSISTENT")
    return {**item, "fixture_sha256": _SHA256}


class SyntheticCapacity:
    """In-memory double with atomic count/credit updates; not a product adapter."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE course(capacity INTEGER NOT NULL, occupied INTEGER NOT NULL);
            CREATE TABLE wallet(credits INTEGER NOT NULL);
            CREATE TABLE booking(actor TEXT PRIMARY KEY);
        """)
        self.db.execute(
            "INSERT INTO course VALUES (?, ?)", (fixture["capacity"], fixture["occupied"])
        )
        self.db.execute("INSERT INTO wallet VALUES (?)", (fixture["credits"],))
        self.db.commit()

    def snapshot(self) -> Snapshot:
        occupied = self.db.execute("SELECT occupied FROM course").fetchone()[0]
        credits = self.db.execute("SELECT credits FROM wallet").fetchone()[0]
        bookings = self.db.execute("SELECT count(*) FROM booking").fetchone()[0]
        return Snapshot(occupied, credits, bookings)

    def availability(self) -> str:
        available = self.db.execute("SELECT occupied < capacity FROM course").fetchone()[0]
        return "AVAILABLE" if available else "FULL"

    def reserve(self, actor: str, *, approved: bool) -> str:
        if not approved:
            return "CONFIRMATION_REQUIRED"
        with self.db:
            if self.db.execute("SELECT 1 FROM booking WHERE actor = ?", (actor,)).fetchone():
                return "ALREADY_BOOKED"
            result = self.db.execute(
                "UPDATE course SET occupied = occupied + 1 WHERE occupied < capacity"
            )
            if result.rowcount != 1:
                return "FULL"
            result = self.db.execute("UPDATE wallet SET credits = credits - 1 WHERE credits > 0")
            if result.rowcount != 1:
                raise ValueError("INSUFFICIENT_CREDITS")
            self.db.execute("INSERT INTO booking(actor) VALUES (?)", (actor,))
            return "BOOKED"

    def close(self) -> None:
        self.db.close()


def validate(
    case_id: str, before: Snapshot, pre_decision: Snapshot, after: Snapshot, response: str
) -> list[str]:
    """Return violated proposed rules; does not judge model wording or product behavior."""
    errors: list[str] = []
    if pre_decision != before:
        errors.append("write_before_decision")
    if case_id == "BOOK-009":
        if response != "BOOKED":
            errors.append("expected_booking")
        if after != Snapshot(before.occupied + 1, before.credits - 1, before.bookings + 1):
            errors.append("last_seat_delta")
    elif case_id == "BOOK-022":
        if response != "FULL":
            errors.append("expected_full")
        if after != before:
            errors.append("full_course_changed_state")
    else:
        raise ValueError("UNSUPPORTED_CASE")
    return errors


def observe(case: Any) -> dict[str, Any]:
    fixture = load(case)
    double = SyntheticCapacity(fixture)
    try:
        before = double.snapshot()
        if case.case_id == "BOOK-009":
            assert double.reserve("synthetic-student", approved=False) == "CONFIRMATION_REQUIRED"
        pre_decision = double.snapshot()
        response = (
            double.reserve("synthetic-student", approved=True)
            if case.case_id == "BOOK-009"
            else double.availability()
        )
        after = double.snapshot()
        violations = validate(case.case_id, before, pre_decision, after, response)
        return {
            "case_id": case.case_id,
            "status": "INCONCLUSIVE",
            "observation_scope": "synthetic_sqlite_double_only",
            "superseded_for_private_slot": case.case_id == "BOOK-009",
            "fixture_sha256": fixture["fixture_sha256"],
            "response": response,
            "before": vars(before),
            "before_decision": vars(pre_decision),
            "after": vars(after),
            "violations": violations,
            "limitations": [
                "No Agent, product capacity implementation, real confirmation, or semantic answer was exercised.",
                *(
                    [
                        "BOOK-009 is a private-coach slot under the user's clarified semantics; this historical group-capacity double is not evidence for that case."
                    ]
                    if case.case_id == "BOOK-009"
                    else []
                ),
            ],
        }
    finally:
        double.close()


def main() -> None:
    from .dataset import load_dataset

    source = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
    cases = {case.case_id: case for case in load_dataset(source).cases}
    report = {
        "report_type": "draft_synthetic_capacity_only",
        "release_eligible": False,
        "results": [observe(cases[case_id]) for case_id in ("BOOK-009", "BOOK-022")],
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
