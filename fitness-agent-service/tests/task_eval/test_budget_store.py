import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.evaluation.task_eval.budget_store import BudgetError, DurableBudget


def test_budget_survives_restart_and_preserves_prior_usage(tmp_path: Path):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100, opening_nano=20)
    ledger.reserve("first", 30)
    ledger.finish("first", usage_known=True)
    reopened = DurableBudget(path, limit_nano=100, opening_nano=20)
    assert reopened.snapshot()["reserved_nano"] == 50
    with pytest.raises(BudgetError, match="exceeded"):
        reopened.reserve("too-much", 51)
    reopened.reserve("second", 50)
    reopened.finish("second", usage_known=True)
    with pytest.raises(BudgetError):
        reopened.reserve("third", 1)


def test_open_existing_never_creates_or_initializes_a_ledger(tmp_path: Path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(BudgetError):
        DurableBudget.open_existing(missing, limit_nano=100, opening_nano=0)
    assert not missing.exists()
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    with pytest.raises(BudgetError):
        DurableBudget.open_existing(empty, limit_nano=100, opening_nano=0)
    assert empty.stat().st_size == 0
    actual = tmp_path / "existing.sqlite"
    ledger = DurableBudget(actual, limit_nano=100, opening_nano=20)
    ledger.reserve("first", 30)
    ledger.finish("first", usage_known=True)
    before = ledger.audit()
    with pytest.raises(BudgetError):
        DurableBudget.open_existing(actual, limit_nano=101, opening_nano=20)
    reopened = DurableBudget.open_existing(actual, limit_nano=100, opening_nano=20)
    assert reopened.audit() == before
    assert reopened.snapshot()["reserved_nano"] == 50
    actual.unlink()
    with pytest.raises(sqlite3.OperationalError):
        reopened.reserve("second", 1)
    assert not actual.exists()


def test_unknown_or_interrupted_call_blocks_later_requests(tmp_path: Path):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100)
    ledger.reserve("unresolved", 10)
    with pytest.raises(BudgetError, match="unresolved"):
        DurableBudget(path, limit_nano=100).reserve("other", 1)
    ledger.finish("unresolved", usage_known=False)
    assert DurableBudget(path, limit_nano=100).snapshot()["frozen"]
    with pytest.raises(BudgetError, match="frozen"):
        ledger.reserve("another", 1)


def test_concurrent_requests_cannot_both_reserve(tmp_path: Path):
    path = tmp_path / "budget.sqlite"
    DurableBudget(path, limit_nano=100)

    def reserve(index: int) -> bool:
        try:
            DurableBudget(path, limit_nano=100).reserve(f"request-{index}", 1)
            return True
        except BudgetError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    assert results.count(True) == 1


def test_budget_identity_and_duplicate_are_checked(tmp_path: Path):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100, opening_nano=5)
    ledger.reserve("r1", 1)
    ledger.finish("r1", usage_known=True)
    with pytest.raises(BudgetError, match="duplicate"):
        ledger.reserve("r1", 1)
    with pytest.raises(BudgetError, match="does not match"):
        DurableBudget(path, limit_nano=101, opening_nano=5)
    with pytest.raises(BudgetError, match="already settled"):
        ledger.finish("r1", usage_known=True)


def test_evidence_submission_is_unverified_and_cannot_unfreeze(tmp_path: Path):
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=100)
    ledger.reserve("unknown", 30)
    ledger.finish("unknown", usage_known=False, reason_code="PROVIDER_HTTP_ERROR")
    result = ledger.submit_reconciliation_evidence(
        "unknown", evidence_sha256="a" * 64, reviewer_ref="reviewer-1", confirmed=True
    )
    assert result == {"evidence_verification": "UNVERIFIED", "unfrozen": False}
    assert ledger.snapshot()["frozen"] and ledger.snapshot()["reserved_nano"] == 30
    with pytest.raises(BudgetError, match="frozen"):
        ledger.reserve("new-request", 1)
    events = ledger.audit()
    assert [e["event"] for e in events] == [
        "AUDIT_STARTED",
        "RESERVED",
        "SETTLED",
        "RECONCILIATION_CLAIM_SUBMITTED",
    ]
    assert events[2]["details"] == {"state": "UNKNOWN", "reason_code": "PROVIDER_HTTP_ERROR"}
    assert events[3]["details"]["confirmation"] == "CALLER_ASSERTED"
    assert events[3]["details"]["evidence_verification"] == "UNVERIFIED"


@pytest.mark.parametrize("state", ["pending", "complete", "missing"])
def test_reconciliation_requires_frozen_unknown(tmp_path: Path, state: str):
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=100)
    ledger.reserve("case", 10)
    if state == "complete":
        ledger.finish("case", usage_known=True)
    with pytest.raises(BudgetError):
        ledger.submit_reconciliation_evidence(
            "missing" if state == "missing" else "case",
            evidence_sha256="a" * 64,
            reviewer_ref="reviewer-1",
            confirmed=True,
        )
    assert not any(e["event"] == "RECONCILIATION_CLAIM_SUBMITTED" for e in ledger.audit())


@pytest.mark.parametrize(
    "digest,reviewer,confirmed",
    [
        ("bad", "reviewer", True),
        ("a" * 64, "", True),
        ("a" * 64, "reviewer", False),
        ("a" * 64, "reviewer", 1),
    ],
)
def test_evidence_submission_requires_explicit_inputs(tmp_path: Path, digest, reviewer, confirmed):
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=100)
    ledger.reserve("case", 10)
    ledger.finish("case", usage_known=False)
    with pytest.raises(BudgetError):
        ledger.submit_reconciliation_evidence(
            "case", evidence_sha256=digest, reviewer_ref=reviewer, confirmed=confirmed
        )
    assert ledger.snapshot()["frozen"]


def test_audit_is_append_only_and_reservation_failure_is_atomic(tmp_path: Path, monkeypatch):
    import sqlite3

    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE budget_audit SET event='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM budget_audit")
    monkeypatch.setattr(
        ledger, "_audit", lambda *_args: (_ for _ in ()).throw(RuntimeError("audit failure"))
    )
    with pytest.raises(RuntimeError):
        ledger.reserve("case", 10)
    assert ledger.snapshot()["reserved_nano"] == 0
    assert ledger.snapshot()["pending"] == 0


def test_legacy_ledger_marks_incomplete_history_without_fabrication(tmp_path: Path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE budget (id INTEGER PRIMARY KEY, limit_nano INTEGER, opening_nano INTEGER, frozen INTEGER)"
        )
        connection.execute(
            "CREATE TABLE reservations (request_id TEXT PRIMARY KEY, amount_nano INTEGER, state TEXT)"
        )
        connection.execute("INSERT INTO budget VALUES (1, 100, 10, 1)")
        connection.execute("INSERT INTO reservations VALUES ('old', 20, 'UNKNOWN')")
    ledger = DurableBudget(path, limit_nano=100, opening_nano=10)
    assert ledger.snapshot()["frozen"] and ledger.snapshot()["reserved_nano"] == 30
    assert ledger.audit()[0]["details"] == {"legacy_reservations": 1}
    assert [e["event"] for e in ledger.audit()] == ["AUDIT_STARTED"]


def test_unknown_diagnostic_settles_safely_without_recording_raw_text(tmp_path: Path):
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=100)
    ledger.reserve("request", 10)
    ledger.finish("request", usage_known=False, reason_code="sensitive provider text")
    assert ledger.snapshot()["frozen"]
    assert ledger.audit()[-1]["details"] == {"state": "UNKNOWN", "reason_code": "UNSPECIFIED"}
    with pytest.raises(BudgetError):
        ledger.submit_reconciliation_evidence(
            "request",
            evidence_sha256="not-a-digest-sensitive",
            reviewer_ref="/unsafe",
            confirmed=True,
        )
    event = ledger.audit()[-1]
    assert event["event"] == "RECONCILIATION_CLAIM_REJECTED"
    assert event["details"] == {"reason": "INVALID_INPUT"}
    assert "sensitive" not in str(event)
