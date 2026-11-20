"""Read selected schema metadata from the disposable booking MySQL, never real data.

This checks only named tables/columns. Their presence cannot prove a usable
business contract, valid fixtures, authorization, a trustworthy audit window,
or a case PASS. It never connects to an arbitrary DSN or modifies the database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .mysql_audit_lab import _sql
from .mysql_restricted_audit import _container
from .sandbox_preflight import PreflightError

# Candidate names are deliberately enumerated, not claimed to exhaust all
# possible schemas. All queries are static and contain no caller input.
_TABLES = ("store", "agent_booking_store", "class_session", "course_session")
_COLUMNS = (
    ("appointment", "store_id"),
    ("appointment", "location_id"),
    ("appointment", "course_session_id"),
    ("course", "store_id"),
    ("course", "capacity"),
    ("course", "max_capacity"),
    ("course", "occupied"),
)


def _sql_query() -> str:
    entries = [
        "'default_database', DATABASE()",
        "'identity', CURRENT_USER()",
        (
            "'schema_present', (SELECT COUNT(*) FROM information_schema.schemata "
            "WHERE schema_name='fitness_eval')"
        ),
    ]
    entries.extend(
        f"'{table}_table', (SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema='fitness_eval' AND table_name='{table}')"
        for table in _TABLES
    )
    entries.extend(
        f"'{table}_{column}', (SELECT COUNT(*) FROM information_schema.columns "
        f"WHERE table_schema='fitness_eval' AND table_name='{table}' AND column_name='{column}')"
        for table, column in _COLUMNS
    )
    return "SELECT JSON_OBJECT(" + ", ".join(entries) + ");"


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_SCHEMA_FIELD")
        result[key] = value
    return result


def observe() -> dict[str, Any]:
    """Observe only metadata on the verified disposable, launcher-owned MySQL."""
    run_id, cid = _container()
    try:
        result = json.loads(_sql(cid, _sql_query()), object_pairs_hook=_unique)
    except (ValueError, TypeError) as exc:
        raise PreflightError("BOOKING_SCHEMA_OBSERVATION_INVALID") from exc
    expected = (
        {"default_database", "identity", "schema_present"}
        | {f"{table}_table" for table in _TABLES}
        | {f"{table}_{column}" for table, column in _COLUMNS}
    )
    if (
        not isinstance(result, dict)
        or set(result) != expected
        or result["default_database"] is not None
        or result["identity"] != "root@localhost"
        or type(result["schema_present"]) is not int
        or result["schema_present"] != 1
        or any(
            type(result[key]) is not int or result[key] not in (0, 1)
            for key in expected - {"default_database", "identity", "schema_present"}
        )
    ):
        raise PreflightError("BOOKING_SCHEMA_OBSERVATION_INVALID")
    return {
        "origin": "disposable_mysql_information_schema",
        "run_id": run_id,
        "mysql_container_id": cid,
        "schema_observed": True,
        "observed_identifiers": {
            key: bool(result[key])
            for key in sorted(expected - {"default_database", "identity", "schema_present"})
        },
        "case_status": {"BOOK-006": "UNKNOWN", "BOOK-009": "UNKNOWN"},
        "business_capability_verified": False,
        "business_data_verified": False,
        "audit_complete": False,
        "limitations": [
            "Only the explicitly named tables and columns were checked; absence does not exclude other schemas.",
            "Schema names do not prove a product API, authoritative store relationships, capacity semantics, or valid business data.",
            "No Agent, business transaction, independent commit window, or case acceptance is evaluated.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args(argv)
    if not Path(args.report_file).is_absolute():
        parser.error("report path must be absolute")
    try:
        descriptor = os.open(
            args.report_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except OSError:
        print(json.dumps({"status": "ERROR", "reason": "REPORT_UNAVAILABLE"}))
        return 1
    try:
        result, exit_code = observe(), 3
    except Exception:  # noqa: BLE001 - database/process errors may contain sensitive details
        result, exit_code = {"status": "ERROR", "reason": "BOOKING_SCHEMA_OBSERVATION_FAILED"}, 1
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(
        json.dumps({"status": "OBSERVED" if exit_code == 3 else "ERROR", "task_status": "UNKNOWN"})
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
