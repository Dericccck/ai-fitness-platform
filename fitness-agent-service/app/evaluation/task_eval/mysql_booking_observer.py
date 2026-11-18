"""Read scoped booking facts from a verified disposable MySQL container.

Only the dedicated task-eval container is accepted. This is an independent
snapshot, not proof that the application audit covers all database writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from typing import Any

from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect

_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_COMMAND = (
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket '
    '--batch --raw --skip-column-names fitness_eval -e "$1"'
)


def _literal(value: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PreflightError("INVALID_FIXTURE_REFERENCE")
    # Hex encoding is independent of SQL quoting and connection SQL mode.
    return "CONVERT(0x" + value.encode("ascii").hex() + " USING utf8mb4)"


def snapshot_booking(
    *,
    run_id: str,
    container_id: str,
    organization_id: str,
    student_id: str,
    contract_id: str,
    request_id: str,
) -> dict[str, Any]:
    """Take a consistent scoped snapshot; never accepts arbitrary SQL or DSNs."""
    org = _literal(organization_id, _ID)
    student = _literal(student_id, _ID)
    contract = _literal(contract_id, _ID)
    request = _literal(request_id, _REQUEST)
    verify_container(_inspect(container_id), run_id=run_id)
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT(
 'database', DATABASE(), 'identity', CURRENT_USER(),
 'contract_count', (SELECT COUNT(*) FROM contract WHERE id={contract} AND organization_id={org} AND user_id={student}),
 'remaining_hours', (SELECT remaining_class_hours FROM contract WHERE id={contract} AND organization_id={org} AND user_id={student} LIMIT 1),
 'appointments', (SELECT COUNT(*) FROM appointment WHERE contract_id={contract} AND organization_id={org} AND user_id={student}),
 'active_appointments', (SELECT COUNT(*) FROM appointment WHERE contract_id={contract} AND organization_id={org} AND user_id={student} AND status=1 AND deleted=0),
 'audit_count', (SELECT COUNT(*) FROM agent_booking_audit WHERE organization_id={org} AND request_id={request}),
 'audit_actions', (SELECT JSON_ARRAYAGG(action) FROM agent_booking_audit WHERE organization_id={org} AND request_id={request}),
 'consumption_count', (SELECT COUNT(*) FROM agent_booking_confirmation_consumption WHERE organization_id={org} AND request_id={request}),
 'linked_audit_and_consumption', (SELECT COUNT(*) FROM agent_booking_audit a JOIN agent_booking_confirmation_consumption c ON a.request_id=c.request_id AND a.organization_id=c.organization_id WHERE a.organization_id={org} AND a.request_id={request})
);
COMMIT;
"""
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "booking-observer", sql],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("BOOKING_SNAPSHOT_FAILED") from exc
    if (
        not isinstance(value, dict)
        or value.pop("database", None) != "fitness_eval"
        or value.pop("identity", None) != "root@localhost"
    ):
        raise PreflightError("DATABASE_IDENTITY_MISMATCH")
    counts = (
        "contract_count",
        "appointments",
        "active_appointments",
        "audit_count",
        "consumption_count",
        "linked_audit_and_consumption",
    )
    if (
        any(type(value.get(key)) is not int or value[key] < 0 for key in counts)
        or value["contract_count"] > 1
        or (value["contract_count"] == 1 and type(value.get("remaining_hours")) is not int)
        or (value["contract_count"] == 0 and value.get("remaining_hours") is not None)
        or (value.get("audit_actions") is not None and not isinstance(value["audit_actions"], list))
        or value["active_appointments"] > value["appointments"]
        or value["linked_audit_and_consumption"]
        > min(value["audit_count"], value["consumption_count"])
        or len(value.get("audit_actions") or []) != value["audit_count"]
        or any(not isinstance(action, str) for action in (value.get("audit_actions") or []))
    ):
        raise PreflightError("INVALID_SNAPSHOT")
    return {
        "origin": "disposable_mysql_consistent_read",
        "snapshot_complete": value["contract_count"] == 1,
        # Booking audit records application operations; it is not a complete
        # independent DB transaction log and cannot prove absence of bypass writes.
        "audit_complete": False,
        "facts": value,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--contract-id", required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        result = snapshot_booking(
            run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
            container_id=os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", ""),
            organization_id=args.organization_id,
            student_id=args.student_id,
            contract_id=args.contract_id,
            request_id=args.request_id,
        )
        print(json.dumps(result))
        return 0
    except Exception as exc:  # noqa: BLE001 - do not disclose database connection details
        reason = str(exc) if isinstance(exc, PreflightError) else "BOOKING_SNAPSHOT_FAILED"
        print(json.dumps({"status": "ERROR", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
