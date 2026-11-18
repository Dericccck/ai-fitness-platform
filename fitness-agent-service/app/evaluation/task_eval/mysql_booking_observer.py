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

from app.proactive.events import ProactiveEventContractError, ProactiveEventMessage

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
    witness_organization_id: str | None = None,
    witness_student_id: str | None = None,
    witness_contract_id: str | None = None,
) -> dict[str, Any]:
    """Take a consistent scoped snapshot; never accepts arbitrary SQL or DSNs."""
    org = _literal(organization_id, _ID)
    student = _literal(student_id, _ID)
    contract = _literal(contract_id, _ID)
    request = _literal(request_id, _REQUEST)
    witness_values = (witness_organization_id, witness_student_id, witness_contract_id)
    if any(item is not None for item in witness_values) and not all(witness_values):
        raise PreflightError("INVALID_WITNESS_REFERENCE")
    witness_enabled = all(item is not None for item in witness_values)
    if witness_enabled:
        if witness_organization_id == organization_id:
            raise PreflightError("INVALID_WITNESS_REFERENCE")
        witness_org = _literal(witness_organization_id, _ID)  # type: ignore[arg-type]
        witness_student = _literal(witness_student_id, _ID)  # type: ignore[arg-type]
        witness_contract = _literal(witness_contract_id, _ID)  # type: ignore[arg-type]
        witness_filter = (
            f"id={witness_contract} AND organization_id={witness_org} AND user_id={witness_student}"
        )
        witness_appointment_filter = f"contract_id={witness_contract} AND organization_id={witness_org} AND user_id={witness_student}"
        witness_sql = (
            f"'witness_contract_count', (SELECT COUNT(*) FROM contract WHERE {witness_filter}), "
            f"'witness_remaining_hours', (SELECT remaining_class_hours FROM contract WHERE {witness_filter} LIMIT 1), "
            f"'witness_appointments', (SELECT COUNT(*) FROM appointment WHERE {witness_appointment_filter}),"
        )
    else:
        witness_sql = "'witness_contract_count', NULL, 'witness_remaining_hours', NULL, 'witness_appointments', NULL,"
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
 'linked_audit_and_consumption', (SELECT COUNT(*) FROM agent_booking_audit a JOIN agent_booking_confirmation_consumption c ON a.request_id=c.request_id AND a.organization_id=c.organization_id WHERE a.organization_id={org} AND a.request_id={request}),
 {witness_sql}
 'global_contracts', (SELECT COUNT(*) FROM contract),
 'global_remaining_hours', (SELECT CAST(COALESCE(SUM(remaining_class_hours),0) AS SIGNED) FROM contract),
 'global_appointments', (SELECT COUNT(*) FROM appointment),
 'global_create_operations', (SELECT COUNT(*) FROM agent_booking_operation),
 'global_reschedule_operations', (SELECT COUNT(*) FROM agent_booking_reschedule_operation),
 'global_cancel_operations', (SELECT COUNT(*) FROM agent_booking_cancel_operation),
 'global_audits', (SELECT COUNT(*) FROM agent_booking_audit),
 'global_consumptions', (SELECT COUNT(*) FROM agent_booking_confirmation_consumption),
 'global_outbox', (SELECT COUNT(*) FROM agent_booking_outbox)
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
        "global_contracts",
        "global_appointments",
        "global_create_operations",
        "global_reschedule_operations",
        "global_cancel_operations",
        "global_audits",
        "global_consumptions",
        "global_outbox",
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
        or type(value.get("global_remaining_hours")) is not int
        or (
            witness_enabled
            and (
                type(value.get("witness_contract_count")) is not int
                or value["witness_contract_count"] != 1
                or type(value.get("witness_remaining_hours")) is not int
                or type(value.get("witness_appointments")) is not int
                or value["witness_appointments"] < 0
            )
        )
        or (
            not witness_enabled
            and any(
                value.get(key) is not None
                for key in (
                    "witness_contract_count",
                    "witness_remaining_hours",
                    "witness_appointments",
                )
            )
        )
    ):
        raise PreflightError("INVALID_SNAPSHOT")
    return {
        "origin": "disposable_mysql_consistent_read",
        "snapshot_complete": value["contract_count"] == 1,
        "witness_observed": witness_enabled,
        # Booking audit records application operations; it is not a complete
        # independent DB transaction log and cannot prove absence of bypass writes.
        "audit_complete": False,
        "facts": value,
    }


def read_created_event(
    *,
    run_id: str,
    container_id: str,
    organization_id: str,
    student_id: str,
    coach_id: str,
    request_id: str,
) -> ProactiveEventMessage:
    """Read the actual create Outbox row and reconstruct the publisher envelope.

    RabbitMQ delivery and the publisher's exact serialized bytes are not observed.
    """
    org = _literal(organization_id, _ID)
    request = _literal(request_id, _REQUEST)
    _literal(student_id, _ID)
    _literal(coach_id, _ID)
    verify_container(_inspect(container_id), run_id=run_id)
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT('database', DATABASE(), 'identity', CURRENT_USER(), 'events',
  (SELECT JSON_ARRAYAGG(JSON_OBJECT(
    'eventId', o.event_key, 'source', 'booking', 'eventType', o.event_type,
    'aggregateId', o.aggregate_id, 'organizationId', o.organization_id,
    'contractVersion', 1, 'aggregateVersion', o.aggregate_version,
    'payload', JSON_EXTRACT(o.payload, '$')))
   FROM agent_booking_outbox o JOIN agent_booking_operation op
   ON op.appointment_id = o.aggregate_id AND op.organization_id = o.organization_id
   WHERE op.request_id = {request} AND op.organization_id = {org}
     AND o.event_type = 'APPOINTMENT_CREATED'));
COMMIT;
"""
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "booking-event", sql],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        response = json.loads(result.stdout)
        if (
            not isinstance(response, dict)
            or response.get("database") != "fitness_eval"
            or response.get("identity") != "root@localhost"
            or not isinstance(response.get("events"), list)
            or len(response["events"]) != 1
        ):
            raise PreflightError("INVALID_BOOKING_EVENT")
        event = ProactiveEventMessage.from_json(json.dumps(response["events"][0]).encode())
        if (
            event.organization_id != organization_id
            or event.event_type != "APPOINTMENT_CREATED"
            or event.source != "booking"
            or event.event_id != f"appointment-created:{event.aggregate_id}"
            or event.aggregate_version != 1
            or event.payload
            != {
                "appointmentId": event.aggregate_id,
                "studentId": student_id,
                "coachId": coach_id,
            }
        ):
            raise PreflightError("INVALID_BOOKING_EVENT")
        return event
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        ProactiveEventContractError,
    ) as exc:
        raise PreflightError("BOOKING_EVENT_READ_FAILED") from exc


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
