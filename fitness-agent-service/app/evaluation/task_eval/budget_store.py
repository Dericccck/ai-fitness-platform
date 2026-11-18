"""Durable, conservative local reservations for paid evaluation requests.

Amounts are integer billionths of a CNY. This ledger is not a provider-side
spending cap and cannot account for other programs using the same account.
Every reservation is committed before the request and is never refunded.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_REVIEWER_REF = re.compile(r"[A-Za-z0-9._:-]{1,80}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = frozenset(
    {
        "UNSPECIFIED",
        "MODEL_CALL_UNCERTAIN",
        "PROVIDER_HTTP_ERROR",
        "RESPONSE_TOO_LARGE",
        "INVALID_RESPONSE",
        "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION",
        "UNEXPECTED_REASONING_OR_TOOL_CALL",
        "MODEL_DID_NOT_FINISH_NORMALLY",
        "MODEL_OUTPUT_MISSING",
        "SENSITIVE_OUTPUT",
        "MODEL_RESPONSE_OR_TRANSPORT_ERROR",
        "MODEL_TIMEOUT",
        "MODEL_TRANSPORT_ERROR",
        "MISSING_TOOL_CALL",
        "UNEXPECTED_REASONING_OR_SERVER_TOOL",
        "INVALID_TOOL_CALL",
        "INVALID_TOOL_ARGUMENTS",
        "FIXTURE_PARAMETERS_MISMATCH",
    }
)


class BudgetError(ValueError):
    """Evaluation budget unavailable, exhausted, or awaiting reconciliation."""


class DurableBudget:
    def __init__(self, path: str | Path, *, limit_nano: int, opening_nano: int = 0) -> None:
        if (
            type(limit_nano) is not int
            or type(opening_nano) is not int
            or not 0 < limit_nano <= 10_000_000_000
            or not 0 <= opening_nano <= limit_nano
        ):
            raise BudgetError("invalid CNY budget")
        self.path = Path(path)
        self.limit_nano = limit_nano
        self.opening_nano = opening_nano
        self._existing_only = False
        with closing(self._connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS budget "
                    "(id INTEGER PRIMARY KEY CHECK (id=1), limit_nano INTEGER NOT NULL, "
                    "opening_nano INTEGER NOT NULL, frozen INTEGER NOT NULL CHECK(frozen IN (0,1)))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS reservations "
                    "(request_id TEXT PRIMARY KEY, amount_nano INTEGER NOT NULL CHECK(amount_nano>0), "
                    "state TEXT NOT NULL CHECK(state IN ('PENDING','COMPLETE','UNKNOWN')))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS budget_audit "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                    "event TEXT NOT NULL, request_id TEXT, details TEXT NOT NULL)"
                )
                # Append-only within the normal API; a database owner can still
                # tamper with or replace a local SQLite file and its triggers.
                connection.execute(
                    "CREATE TRIGGER IF NOT EXISTS budget_audit_no_update BEFORE UPDATE ON budget_audit "
                    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END"
                )
                connection.execute(
                    "CREATE TRIGGER IF NOT EXISTS budget_audit_no_delete BEFORE DELETE ON budget_audit "
                    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO budget VALUES (1, ?, ?, 0)",
                    (limit_nano, opening_nano),
                )
                row = connection.execute(
                    "SELECT limit_nano, opening_nano FROM budget WHERE id=1"
                ).fetchone()
                if row != (limit_nano, opening_nano):
                    raise BudgetError("existing budget does not match requested limit or opening")
                if connection.execute("SELECT COUNT(*) FROM budget_audit").fetchone()[0] == 0:
                    legacy = connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
                    self._audit(connection, "AUDIT_STARTED", None, {"legacy_reservations": legacy})
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @classmethod
    def open_existing(
        cls, path: str | Path, *, limit_nano: int, opening_nano: int
    ) -> DurableBudget:
        """Open an already initialized ledger without creating or repairing it."""
        location = Path(path)
        if (
            location.is_symlink()
            or not location.is_file()
            or type(limit_nano) is not int
            or type(opening_nano) is not int
            or not 0 < limit_nano <= 10_000_000_000
            or not 0 <= opening_nano <= limit_nano
        ):
            raise BudgetError("existing budget unavailable or invalid")
        ledger = cls.__new__(cls)
        ledger.path = location.resolve()
        ledger.limit_nano = limit_nano
        ledger.opening_nano = opening_nano
        ledger._existing_only = True
        try:
            with closing(ledger._connection()) as connection:
                row = connection.execute(
                    "SELECT limit_nano, opening_nano, frozen FROM budget WHERE id=1"
                ).fetchone()
                audit_count = connection.execute("SELECT COUNT(*) FROM budget_audit").fetchone()[0]
                invalid = connection.execute(
                    "SELECT COUNT(*) FROM reservations WHERE amount_nano<=0 OR "
                    "state NOT IN ('PENDING','COMPLETE','UNKNOWN') OR amount_nano IS NULL OR state IS NULL"
                ).fetchone()[0]
                if row is None or row[:2] != (limit_nano, opening_nano) or row[2] not in (0, 1):
                    raise BudgetError("existing budget identity mismatch or invalid")
                if not audit_count or invalid:
                    raise BudgetError("existing budget history is incomplete or invalid")
            snapshot = ledger.snapshot()
            if not opening_nano <= snapshot["reserved_nano"] <= limit_nano:
                raise BudgetError("existing budget history is invalid")
        except sqlite3.Error as exc:
            raise BudgetError("existing budget unavailable or invalid") from exc
        return ledger

    def _connection(self) -> sqlite3.Connection:
        if self._existing_only:
            return sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None
            )
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    @staticmethod
    def _audit(
        connection: sqlite3.Connection, event: str, request_id: str | None, details: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO budget_audit(event, request_id, details) VALUES (?, ?, ?)",
            (event, request_id, json.dumps(details, sort_keys=True, separators=(",", ":"))),
        )

    def reserve(self, request_id: str, amount_nano: int) -> None:
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID.fullmatch(request_id)
            or type(amount_nano) is not int
            or amount_nano <= 0
        ):
            raise BudgetError("invalid reservation")
        with closing(self._connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT limit_nano, opening_nano, frozen FROM budget WHERE id=1"
                ).fetchone()
                if row is None or row[2]:
                    raise BudgetError("budget missing or frozen")
                pending = connection.execute(
                    "SELECT COUNT(*) FROM reservations WHERE state='PENDING'"
                ).fetchone()[0]
                total = connection.execute(
                    "SELECT COALESCE(SUM(amount_nano), 0) FROM reservations"
                ).fetchone()[0]
                if pending or row[1] + total + amount_nano > row[0]:
                    raise BudgetError("budget exceeded or an earlier request is unresolved")
                try:
                    connection.execute(
                        "INSERT INTO reservations VALUES (?, ?, 'PENDING')",
                        (request_id, amount_nano),
                    )
                except sqlite3.IntegrityError as exc:
                    raise BudgetError("duplicate request id") from exc
                self._audit(connection, "RESERVED", request_id, {"amount_nano": amount_nano})
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def finish(
        self, request_id: str, *, usage_known: bool, reason_code: str = "UNSPECIFIED"
    ) -> None:
        if (
            type(usage_known) is not bool
            or not isinstance(request_id, str)
            or not _REQUEST_ID.fullmatch(request_id)
            or not isinstance(reason_code, str)
        ):
            raise BudgetError("invalid settlement")
        # A newly introduced diagnostic must never prevent fail-closed
        # settlement, and arbitrary exception text must not enter the audit.
        if reason_code not in _REASONS:
            reason_code = "UNSPECIFIED"
        with closing(self._connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = "COMPLETE" if usage_known else "UNKNOWN"
                result = connection.execute(
                    "UPDATE reservations SET state=? WHERE request_id=? AND state='PENDING'",
                    (state, request_id),
                )
                if result.rowcount != 1:
                    raise BudgetError("reservation missing or already settled")
                if not usage_known:
                    connection.execute("UPDATE budget SET frozen=1 WHERE id=1")
                self._audit(
                    connection, "SETTLED", request_id, {"state": state, "reason_code": reason_code}
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def submit_reconciliation_evidence(
        self, request_id: str, *, evidence_sha256: str, reviewer_ref: str, confirmed: bool
    ) -> dict[str, Any]:
        """Record an unverified claim; this cannot clear UNKNOWN or unfreeze.

        A digest is not proof of authenticity, completeness or a bill's match
        to this request. The reviewer reference and confirmation are asserted by
        the caller, not authenticated. No evidence contents or credentials are stored.
        """
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID.fullmatch(request_id)
            or not isinstance(evidence_sha256, str)
            or not _SHA256.fullmatch(evidence_sha256)
            or not isinstance(reviewer_ref, str)
            or not _REVIEWER_REF.fullmatch(reviewer_ref)
            or confirmed is not True
        ):
            safe_id = (
                request_id
                if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id)
                else None
            )
            with closing(self._connection()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._audit(
                    connection,
                    "RECONCILIATION_CLAIM_REJECTED",
                    safe_id,
                    {"reason": "INVALID_INPUT"},
                )
                connection.commit()
            raise BudgetError("invalid reconciliation submission")
        with closing(self._connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                budget = connection.execute("SELECT frozen FROM budget WHERE id=1").fetchone()
                reservation = connection.execute(
                    "SELECT state FROM reservations WHERE request_id=?", (request_id,)
                ).fetchone()
                if budget is None or budget[0] != 1 or reservation != ("UNKNOWN",):
                    self._audit(
                        connection,
                        "RECONCILIATION_CLAIM_REJECTED",
                        request_id,
                        {"reason": "NOT_FROZEN_UNKNOWN"},
                    )
                    connection.commit()
                    raise BudgetError("only frozen UNKNOWN reservations may be submitted")
                self._audit(
                    connection,
                    "RECONCILIATION_CLAIM_SUBMITTED",
                    request_id,
                    {
                        "evidence_sha256": evidence_sha256,
                        "reviewer_ref": reviewer_ref,
                        "confirmation": "CALLER_ASSERTED",
                        "evidence_verification": "UNVERIFIED",
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"evidence_verification": "UNVERIFIED", "unfrozen": False}

    def audit(self) -> list[dict[str, Any]]:
        """Read events recorded since audit installation, excluding raw evidence."""
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT id, created_at, event, request_id, details FROM budget_audit ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "event": row[2],
                "request_id": row[3],
                "details": json.loads(row[4]),
            }
            for row in rows
        ]

    def snapshot(self) -> dict[str, Any]:
        with closing(self._connection()) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT limit_nano, opening_nano, frozen FROM budget WHERE id=1"
            ).fetchone()
            if row is None:
                raise BudgetError("budget missing")
            used, pending = connection.execute(
                "SELECT COALESCE(SUM(amount_nano), 0), "
                "COALESCE(SUM(CASE WHEN state='PENDING' THEN 1 ELSE 0 END), 0) "
                "FROM reservations"
            ).fetchone()
            connection.commit()
            return {
                "limit_nano": row[0],
                "reserved_nano": row[1] + used,
                "frozen": bool(row[2]),
                "pending": pending,
                "provider_cap_verified": False,
            }
