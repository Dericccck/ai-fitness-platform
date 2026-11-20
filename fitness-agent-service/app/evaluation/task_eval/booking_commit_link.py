"""Correlate one local confirmation and booking commit without exporting secrets.

This is a diagnostic observation, not a complete audit or cross-database clock.
The PostgreSQL record/events come from the application's repository and the
MySQL snapshot comes from the launcher-owned disposable database.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .mysql_booking_observer import _ID, _REQUEST, _literal
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect


def read_commit_link(*, run_id: str, container_id: str, record: Any) -> dict[str, Any]:
    """Read counts and binding comparisons in one consistent MySQL snapshot."""
    request = _literal(record.request_id, _REQUEST)
    confirmation = _literal(record.id, _REQUEST)
    jti = _literal(record.credential_jti, _REQUEST)
    org = _literal(record.organization_id, _ID)
    actor = _literal(record.subject_user_id, _ID)
    tool = _literal(record.tool_id, _REQUEST)
    action = _literal(record.action, _REQUEST)
    payload = _literal(record.payload_hash, _REQUEST)
    resource = _literal(
        record.resource_id or f"{record.organization_id}:{record.subject_user_id}", _REQUEST
    )
    verify_container(_inspect(container_id), run_id=run_id)
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT('database', DATABASE(), 'identity', CURRENT_USER(),
 'consumptions', (SELECT COUNT(*) FROM agent_booking_confirmation_consumption WHERE BINARY request_id=BINARY {request}),
 'operations', (SELECT COUNT(*) FROM agent_booking_operation WHERE BINARY request_id=BINARY {request}),
 'audits', (SELECT COUNT(*) FROM agent_booking_audit WHERE BINARY request_id=BINARY {request}),
 'linked', (SELECT COUNT(*) FROM agent_booking_confirmation_consumption c
   JOIN agent_booking_operation o ON BINARY o.request_id=BINARY c.request_id
   JOIN agent_booking_audit a ON BINARY a.request_id=BINARY c.request_id AND BINARY a.appointment_id=BINARY o.appointment_id
   JOIN appointment p ON BINARY p.id=BINARY o.appointment_id
   JOIN agent_booking_outbox x ON BINARY x.aggregate_id=BINARY p.id AND BINARY x.event_type=BINARY 'APPOINTMENT_CREATED'
   WHERE BINARY c.request_id=BINARY {request} AND BINARY c.confirmation_id=BINARY {confirmation} AND BINARY c.jti=BINARY {jti}
     AND BINARY c.tool_id=BINARY {tool} AND BINARY c.action=BINARY {action} AND BINARY c.payload_hash=BINARY {payload}
     AND BINARY c.resource=BINARY {resource} AND BINARY c.organization_id=BINARY {org}
     AND BINARY o.organization_id=BINARY {org} AND BINARY o.actor_id=BINARY {actor}
     AND BINARY a.organization_id=BINARY {org} AND BINARY a.actor_id=BINARY {actor} AND BINARY a.action=BINARY 'CREATE_APPOINTMENT'
     AND BINARY p.organization_id=BINARY {org} AND BINARY p.user_id=BINARY {actor} AND p.status=1 AND p.deleted=0
     AND BINARY x.organization_id=BINARY {org}));
COMMIT;
"""
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container_id,
                "sh",
                "-c",
                (
                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket '
                    "--batch --raw --skip-column-names fitness_eval"
                ),
            ],
            input=sql,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("COMMIT_LINK_READ_FAILED") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"database", "identity", "consumptions", "operations", "audits", "linked"}
        or value["database"] != "fitness_eval"
        or value["identity"] != "root@localhost"
        or any(
            type(value[k]) is not int or value[k] < 0
            for k in ("consumptions", "operations", "audits", "linked")
        )
    ):
        raise PreflightError("COMMIT_LINK_INVALID")
    return {key: value[key] for key in ("consumptions", "operations", "audits", "linked")}


def evaluate_link(record: Any, events: list[Any], counts: dict[str, Any]) -> dict[str, Any]:
    """Report binding consistency; neither timestamps nor app logs prove causality."""
    keys = ("consumptions", "operations", "audits", "linked")
    if set(counts) != set(keys) or any(type(counts[k]) is not int or counts[k] < 0 for k in keys):
        raise ValueError("INVALID_LINK_COUNTS")
    approved = [e for e in events if e.event_type == "APPROVED"]
    issued = [e for e in events if e.event_type == "ISSUED"]
    succeeded = [e for e in events if e.event_type == "EXECUTION_SUCCEEDED"]
    events_match = bool(
        len(approved) == len(issued) == len(succeeded) == 1
        and all(
            e.confirmation_id == record.id and e.request_id == record.request_id
            for e in (*approved, *issued, *succeeded)
        )
        and approved[0].actor_user_id == record.subject_user_id
        and approved[0].decision_request_id == record.decision_request_id
        and approved[0].authorization_status
        == issued[0].authorization_status
        == succeeded[0].authorization_status
        == "APPROVED"
        and approved[0].authorization_version
        < issued[0].authorization_version
        < succeeded[0].authorization_version
    )
    matches = bool(
        record.authorization_status == "APPROVED"
        and record.execution_status == "SUCCEEDED"
        and record.credential_jti
        and record.decision_request_id
        and events_match
        and all(counts[k] == 1 for k in keys)
    )
    return {
        "status": "MATCH" if matches else "MISMATCH",
        "postgres_event_chain_matches": events_match,
        "mysql_counts": counts,
        "origin": "application_postgres_repository_and_disposable_mysql_consistent_read",
        "independent_provenance": False,
        "cross_database_commit_order_proven": False,
        "audit_window_complete": False,
        "transaction_settlement_proven": False,
    }
