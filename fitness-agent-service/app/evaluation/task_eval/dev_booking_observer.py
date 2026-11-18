"""Read development-trial booking details from the verified disposable MySQL only.

This read-only snapshot does not claim to be a complete transaction audit.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .mysql_booking_observer import _COMMAND, _ID, _literal
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect


def read_booking_details(
    *, run_id: str, container_id: str, organization_id: str, student_id: str, contract_id: str
) -> dict[str, Any]:
    org = _literal(organization_id, _ID)
    student = _literal(student_id, _ID)
    contract = _literal(contract_id, _ID)
    verify_container(_inspect(container_id), run_id=run_id)
    # JDBC uses serverTimezone=UTC in the isolated launcher, so DATETIME values
    # are interpreted as UTC for this fixture only.
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT('database', DATABASE(), 'identity', CURRENT_USER(), 'rows',
  (SELECT JSON_ARRAYAGG(JSON_OBJECT(
    'courseId', course_id, 'coachId', coach_id, 'studentId', user_id,
    'organizationId', organization_id, 'contractId', contract_id,
    'startTime', DATE_FORMAT(course_start_time, '%Y-%m-%dT%H:%i:%sZ'),
    'endTime', DATE_FORMAT(course_end_time, '%Y-%m-%dT%H:%i:%sZ')))
   FROM appointment WHERE organization_id={org} AND user_id={student}
     AND contract_id={contract} AND status=1 AND deleted=0));
COMMIT;
"""
    try:
        output = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "dev-details", sql],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        value = json.loads(output.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("BOOKING_DETAIL_READ_FAILED") from exc
    if (
        not isinstance(value, dict)
        or value.get("database") != "fitness_eval"
        or value.get("identity") != "root@localhost"
    ):
        raise PreflightError("BOOKING_DETAIL_IDENTITY_MISMATCH")
    rows = value.get("rows")
    if rows is None:
        rows = []
    keys = {
        "courseId",
        "coachId",
        "studentId",
        "organizationId",
        "contractId",
        "startTime",
        "endTime",
    }
    if (
        not isinstance(rows, list)
        or len(rows) > 10
        or any(
            not isinstance(row, dict)
            or set(row) != keys
            or any(not isinstance(row[key], str) or not row[key] for key in keys)
            or row["organizationId"] != organization_id
            or row["studentId"] != student_id
            or row["contractId"] != contract_id
            for row in rows
        )
    ):
        raise PreflightError("INVALID_BOOKING_DETAILS")
    rows.sort(key=lambda row: (row["startTime"], row["endTime"], row["courseId"], row["coachId"]))
    return {
        "snapshot_complete": True,
        "created_bookings": rows,
        "origin": "disposable_mysql_consistent_read",
    }
