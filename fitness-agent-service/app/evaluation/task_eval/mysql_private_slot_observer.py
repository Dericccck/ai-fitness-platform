"""Read a private-coach slot from a verified disposable MySQL, never a live DSN.

This snapshot follows the BookingRepository overlap predicate. It does not
prove that pending transactions have drained or establish authorization order.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

from .mysql_booking_observer import _COMMAND, _ID, _literal
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect


def _time(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise PreflightError("INVALID_SLOT_TIME")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None or parsed.microsecond:
            raise ValueError("timezone or precision")
        utc = parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise PreflightError("INVALID_SLOT_TIME") from exc
    return utc.strftime("%Y-%m-%d %H:%M:%S"), utc.isoformat().replace("+00:00", "Z")


def read_private_slot(
    *,
    run_id: str,
    container_id: str,
    organization_id: str,
    coach_id: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """Read matching active appointments, including temporary coach assignments."""
    org, coach = _literal(organization_id, _ID), _literal(coach_id, _ID)
    start_sql, start_iso = _time(start_time)
    end_sql, end_iso = _time(end_time)
    if start_iso >= end_iso:
        raise PreflightError("INVALID_SLOT_TIME")
    # Encode timestamp literals independently of SQL quoting and SQL mode.
    start_literal = "CONVERT(0x" + start_sql.encode("ascii").hex() + " USING utf8mb4)"
    end_literal = "CONVERT(0x" + end_sql.encode("ascii").hex() + " USING utf8mb4)"
    verify_container(_inspect(container_id), run_id=run_id)
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT('database', DATABASE(), 'identity', CURRENT_USER(), 'rows',
 (SELECT JSON_ARRAYAGG(JSON_OBJECT(
   'id', id, 'organizationId', organization_id, 'studentId', user_id,
   'coachId', coach_id, 'temporaryCoachId', temp_coach_id, 'courseId', course_id,
   'startTime', DATE_FORMAT(course_start_time, '%Y-%m-%dT%H:%i:%sZ'),
   'endTime', DATE_FORMAT(course_end_time, '%Y-%m-%dT%H:%i:%sZ')))
  FROM appointment WHERE organization_id={org} AND deleted=0
   AND (coach_id={coach} OR temp_coach_id={coach}) AND status IN (0,1,3,4,5)
   AND course_start_time < {end_literal}
   AND (course_end_time IS NULL OR course_end_time > {start_literal})));
COMMIT;
"""
    try:
        output = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "private-slot", sql],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        value = json.loads(output.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("PRIVATE_SLOT_READ_FAILED") from exc
    if (
        not isinstance(value, dict)
        or value.get("database") != "fitness_eval"
        or value.get("identity") != "root@localhost"
    ):
        raise PreflightError("PRIVATE_SLOT_IDENTITY_MISMATCH")
    rows = value.get("rows")
    if rows is None:
        rows = []
    required = {
        "id",
        "organizationId",
        "studentId",
        "coachId",
        "temporaryCoachId",
        "courseId",
        "startTime",
        "endTime",
    }
    if (
        not isinstance(rows, list)
        or len(rows) > 100
        or any(
            not isinstance(row, dict)
            or set(row) != required
            or any(
                not isinstance(row[key], str) or not row[key]
                for key in required - {"temporaryCoachId", "endTime"}
            )
            or (
                row["temporaryCoachId"] is not None and not isinstance(row["temporaryCoachId"], str)
            )
            or (row["endTime"] is not None and not isinstance(row["endTime"], str))
            or row["organizationId"] != organization_id
            or coach_id not in (row["coachId"], row["temporaryCoachId"])
            for row in rows
        )
        or len({row["id"] for row in rows}) != len(rows)
    ):
        raise PreflightError("PRIVATE_SLOT_ROWS_INVALID")
    rows.sort(key=lambda row: row["id"])
    return {
        "origin": "disposable_mysql_consistent_read",
        "scope": {
            "organizationId": organization_id,
            "coachId": coach_id,
            "startTime": start_iso,
            "endTime": end_iso,
        },
        "rows": rows,
        "snapshot_complete": True,
        "settlement_proven": False,
        "limitations": [
            "A read-only snapshot does not prove transaction settlement or an uninterrupted audit window."
        ],
    }
