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


def test_explicit_unknown_cost_resume_keeps_reservation_and_audit(tmp_path: Path):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100, opening_nano=10)
    ledger.reserve("case", 20)
    ledger.finish("case", usage_known=False)
    resumed = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=100,
        opening_nano=10,
        expected_reserved_nano=30,
        expected_unknown={"case": 20},
        authorization_ref="user-turn-1",
        acknowledge_unknown_cost=True,
    )
    assert resumed.snapshot()["reserved_nano"] == 30
    assert not resumed.snapshot()["frozen"]
    event = resumed.audit()[-1]
    assert event["event"] == "UNKNOWN_COST_RESUME_AUTHORIZED"
    assert event["details"]["unknown_cost"] == "UNVERIFIED_AND_NOT_REFUNDED"
    assert event["details"]["unknown_reservations"] == {"case": 20}
    assert event["details"]["legacy_history_incomplete"] is False
    resumed.reserve("next", 70)
    resumed.finish("next", usage_known=True)
    with pytest.raises(BudgetError, match="exceeded"):
        resumed.reserve("too-much", 1)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state FROM reservations WHERE request_id='case'"
        ).fetchone() == ("UNKNOWN",)


def test_legacy_unknown_resume_creates_honest_audit_in_one_transaction(tmp_path: Path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE budget (id INTEGER PRIMARY KEY, limit_nano INTEGER, opening_nano INTEGER, frozen INTEGER)"
        )
        connection.execute(
            "CREATE TABLE reservations (request_id TEXT PRIMARY KEY, amount_nano INTEGER, state TEXT)"
        )
        connection.execute("INSERT INTO budget VALUES (1, 100, 10, 1)")
        connection.execute("INSERT INTO reservations VALUES ('old', 20, 'UNKNOWN')")
    ledger = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=100,
        opening_nano=10,
        expected_reserved_nano=30,
        expected_unknown={"old": 20},
        authorization_ref="user-turn-1",
        acknowledge_unknown_cost=True,
    )
    assert ledger.audit()[0]["details"] == {"legacy_reservations": 1}
    assert ledger.audit()[1]["details"]["legacy_history_incomplete"] is True
    assert len(ledger.audit()[1]["details"]["prior_snapshot_sha256"]) == 64
    assert ledger.snapshot()["reserved_nano"] == 30


def test_legacy_unknown_allows_a_new_audited_unknown_to_resume_again(tmp_path: Path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE budget (id INTEGER PRIMARY KEY, limit_nano INTEGER, opening_nano INTEGER, frozen INTEGER)"
        )
        connection.execute(
            "CREATE TABLE reservations (request_id TEXT PRIMARY KEY, amount_nano INTEGER, state TEXT)"
        )
        connection.execute("INSERT INTO budget VALUES (1, 100, 10, 1)")
        connection.execute("INSERT INTO reservations VALUES ('old', 20, 'UNKNOWN')")
    ledger = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=100,
        opening_nano=10,
        expected_reserved_nano=30,
        expected_unknown={"old": 20},
        authorization_ref="first",
        acknowledge_unknown_cost=True,
    )
    ledger.reserve("new", 5)
    ledger.finish("new", usage_known=False)
    ledger = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=100,
        opening_nano=10,
        expected_reserved_nano=35,
        expected_unknown={"old": 20, "new": 5},
        authorization_ref="second",
        acknowledge_unknown_cost=True,
    )
    assert ledger.snapshot()["reserved_nano"] == 35
    assert not ledger.snapshot()["frozen"]
    assert [event["event"] for event in ledger.audit()].count("UNKNOWN_COST_RESUME_AUTHORIZED") == 2


@pytest.mark.parametrize("tamper", ["add_unknown", "change_old_amount", "unrelated_freeze"])
def test_second_resume_rejects_unrecorded_unknown_or_freeze(tmp_path: Path, tamper: str):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE budget (id INTEGER PRIMARY KEY, limit_nano INTEGER, opening_nano INTEGER, frozen INTEGER)"
        )
        connection.execute(
            "CREATE TABLE reservations (request_id TEXT PRIMARY KEY, amount_nano INTEGER, state TEXT)"
        )
        connection.execute("INSERT INTO budget VALUES (1, 100, 10, 1)")
        connection.execute("INSERT INTO reservations VALUES ('old', 20, 'UNKNOWN')")
    ledger = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=100,
        opening_nano=10,
        expected_reserved_nano=30,
        expected_unknown={"old": 20},
        authorization_ref="first",
        acknowledge_unknown_cost=True,
    )
    if tamper != "unrelated_freeze":
        ledger.reserve("new", 5)
        ledger.finish("new", usage_known=False)
    with sqlite3.connect(path) as connection:
        if tamper == "add_unknown":
            connection.execute("INSERT INTO reservations VALUES ('extra', 1, 'UNKNOWN')")
        elif tamper == "change_old_amount":
            connection.execute("UPDATE reservations SET amount_nano=21 WHERE request_id='old'")
        else:
            connection.execute("UPDATE budget SET frozen=1")
        rows = connection.execute(
            "SELECT request_id, amount_nano FROM reservations WHERE state='UNKNOWN'"
        ).fetchall()
    before = ledger.audit()
    with pytest.raises(BudgetError):
        DurableBudget.resume_with_unknown_cost(
            path,
            limit_nano=100,
            opening_nano=10,
            expected_reserved_nano=10 + sum(x[1] for x in rows),
            expected_unknown=dict(rows),
            authorization_ref="second",
            acknowledge_unknown_cost=True,
        )
    assert ledger.snapshot()["frozen"]
    assert ledger.audit() == before


@pytest.mark.parametrize(
    "change",
    ["no_authorization", "wrong_amount", "wrong_id", "pending", "bare_freeze", "wrong_limit"],
)
def test_unknown_resume_rejects_changed_or_unrelated_state(tmp_path: Path, change: str):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100, opening_nano=10)
    ledger.reserve("case", 20)
    if change != "pending":
        ledger.finish("case", usage_known=change == "bare_freeze")
    if change in ("pending", "bare_freeze"):
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE budget SET frozen=1")
    before = ledger.audit()
    args = {
        "limit_nano": 100,
        "opening_nano": 10,
        "expected_reserved_nano": 30,
        "expected_unknown": {"case": 20},
        "authorization_ref": "user-turn-1",
        "acknowledge_unknown_cost": True,
    }
    if change == "no_authorization":
        args["acknowledge_unknown_cost"] = False
    elif change == "wrong_amount":
        args["expected_reserved_nano"] = 29
    elif change == "wrong_id":
        args["expected_unknown"] = {"other": 20}
    elif change == "wrong_limit":
        args["limit_nano"] = 101
    with pytest.raises(BudgetError):
        DurableBudget.resume_with_unknown_cost(path, **args)
    assert ledger.snapshot()["frozen"]
    assert ledger.audit() == before


def test_resume_rollback_when_audit_fails(tmp_path: Path, monkeypatch):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100)
    ledger.reserve("case", 20)
    ledger.finish("case", usage_known=False)
    before = ledger.audit()
    monkeypatch.setattr(
        DurableBudget, "_audit", lambda *_args: (_ for _ in ()).throw(RuntimeError("audit failure"))
    )
    with pytest.raises(RuntimeError):
        DurableBudget.resume_with_unknown_cost(
            path,
            limit_nano=100,
            opening_nano=0,
            expected_reserved_nano=20,
            expected_unknown={"case": 20},
            authorization_ref="user-turn-1",
            acknowledge_unknown_cost=True,
        )
    assert ledger.snapshot()["frozen"]
    assert ledger.audit() == before


def test_authorized_increase_preserves_unknown_freeze_and_history(tmp_path):
    path = tmp_path / "budget.sqlite"
    old = DurableBudget(path, limit_nano=10_000_000_000, opening_nano=46_080_000)
    old.reserve("known", 102_400_000)
    old.finish("known", usage_known=True)
    old.reserve("unknown", 204_800_000)
    old.finish("unknown", usage_known=False)
    before = old.audit()
    upgraded = DurableBudget.increase_limit(
        path,
        previous_limit_nano=10_000_000_000,
        new_limit_nano=30_000_000_000,
        opening_nano=46_080_000,
        expected_reserved_nano=353_280_000,
        expected_unknown={"unknown": 204_800_000},
        authorization_ref="test-user-authorized",
        acknowledge_unknown_cost=True,
        explicitly_authorized=True,
    )
    assert upgraded.snapshot() == {
        "limit_nano": 30_000_000_000,
        "reserved_nano": 353_280_000,
        "frozen": True,
        "pending": 0,
        "provider_cap_verified": False,
    }
    assert upgraded.audit()[:-1] == before
    event = upgraded.audit()[-1]
    assert event["event"] == "LIMIT_INCREASED"
    assert event["details"]["unknown_reservations"] == {"unknown": 204_800_000}
    assert event["details"]["unknown_cost"] == "UNVERIFIED_AND_NOT_REFUNDED"
    assert event["details"]["previous_frozen"] is True
    assert event["details"]["provider_cap_verified"] is False
    with pytest.raises(BudgetError):
        upgraded.reserve("still-frozen", 1)
    resumed = DurableBudget.resume_with_unknown_cost(
        path,
        limit_nano=30_000_000_000,
        opening_nano=46_080_000,
        expected_reserved_nano=353_280_000,
        expected_unknown={"unknown": 204_800_000},
        authorization_ref="test-separate-resume",
        acknowledge_unknown_cost=True,
    )
    assert resumed.snapshot()["reserved_nano"] == 353_280_000
    with pytest.raises(BudgetError, match="identity changed"):
        old.reserve("stale-handle", 1)
    resumed.reserve("new", 25_000_000_000)
    with pytest.raises(BudgetError, match="identity changed"):
        old.finish("new", usage_known=True)
    resumed.finish("new", usage_known=True)
    assert (
        DurableBudget(path, limit_nano=30_000_000_000, opening_nano=46_080_000).snapshot()[
            "reserved_nano"
        ]
        == 25_353_280_000
    )


@pytest.mark.parametrize(
    "change",
    [
        "authorization",
        "unknown_ack",
        "wrong_unknown",
        "wrong_total",
        "same_limit",
        "too_high",
        "missing",
        "pending",
    ],
)
def test_increase_rejects_unapproved_changed_or_unsettled_state(tmp_path, change):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100, opening_nano=10)
    ledger.reserve("request", 20)
    if change != "pending":
        ledger.finish("request", usage_known=False)
    before = ledger.audit()
    kwargs = {
        "previous_limit_nano": 100,
        "new_limit_nano": 200,
        "opening_nano": 10,
        "expected_reserved_nano": 30,
        "expected_unknown": {"request": 20},
        "authorization_ref": "test-user",
        "acknowledge_unknown_cost": True,
        "explicitly_authorized": True,
    }
    if change == "authorization":
        kwargs["explicitly_authorized"] = False
    elif change == "unknown_ack":
        kwargs["acknowledge_unknown_cost"] = False
    elif change == "wrong_unknown":
        kwargs["expected_unknown"] = {"request": 21}
    elif change == "wrong_total":
        kwargs["expected_reserved_nano"] = 31
    elif change == "same_limit":
        kwargs["new_limit_nano"] = 100
    elif change == "too_high":
        kwargs["new_limit_nano"] = 30_000_000_001
    elif change == "missing":
        path = tmp_path / "missing.sqlite"
    elif change == "pending":
        kwargs["expected_unknown"] = {}
    with pytest.raises(BudgetError):
        DurableBudget.increase_limit(path, **kwargs)
    assert ledger.snapshot()["limit_nano"] == 100 and ledger.audit() == before
    if change == "missing":
        assert not path.exists()


def test_cannot_create_or_open_increased_ledger_without_audit(tmp_path):
    path = tmp_path / "budget.sqlite"
    with pytest.raises(BudgetError):
        DurableBudget(path, limit_nano=30_000_000_000)
    assert not path.exists()
    DurableBudget(path, limit_nano=10_000_000_000)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE budget SET limit_nano=30000000000")
    with pytest.raises(BudgetError, match="authorization"):
        DurableBudget.open_existing(path, limit_nano=30_000_000_000, opening_nano=0)


def test_limit_increase_rolls_back_if_audit_write_fails(tmp_path, monkeypatch):
    path = tmp_path / "budget.sqlite"
    ledger = DurableBudget(path, limit_nano=100)
    before = ledger.audit()
    monkeypatch.setattr(
        DurableBudget, "_audit", lambda *_: (_ for _ in ()).throw(RuntimeError("audit error"))
    )
    with pytest.raises(RuntimeError):
        DurableBudget.increase_limit(
            path,
            previous_limit_nano=100,
            new_limit_nano=200,
            opening_nano=0,
            expected_reserved_nano=0,
            expected_unknown={},
            authorization_ref="test-user",
            acknowledge_unknown_cost=False,
            explicitly_authorized=True,
        )
    assert ledger.snapshot()["limit_nano"] == 100 and ledger.audit() == before
