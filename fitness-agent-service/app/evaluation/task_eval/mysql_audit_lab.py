"""Test bounded MySQL transaction auditing on an empty, verified disposable container.

This executable lab never connects to an application endpoint. Audit rows share
transactions with watched tables; it demonstrates committed row mutations within
one test database, not PostgreSQL confirmation causality or independent production
provenance. The restricted writer is unable to mutate audit data or trigger DDL.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from .mysql_sandbox_preflight import check_mysql, verify_container
from .sandbox_preflight import PreflightError, _inspect

_TABLES = (
    "contract",
    "appointment",
    "agent_booking_operation",
    "agent_booking_confirmation_consumption",
    "agent_booking_audit",
    "agent_booking_outbox",
    "agent_booking_resource_lock",
    "agent_booking_reschedule_operation",
    "agent_booking_cancel_operation",
    "agent_booking_outbox_replay_audit",
)
_EVENTS = ("INSERT", "UPDATE", "DELETE")
_ROOT_CMD = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket -uroot --batch --raw --skip-column-names'
_WRITER_CMD = 'MYSQL_PWD="$TASK_EVAL_AUDIT_WRITER_PASSWORD" exec mysql --protocol=socket -ueval_writer --batch --raw --skip-column-names'
_ROOT = Path(__file__).resolve().parents[4]


def _sql(container_id: str, sql: str, *, writer: bool = False, deny: bool = False) -> str:
    args = ["docker", "exec", "-i"]
    if writer:
        args.extend(["-e", "TASK_EVAL_AUDIT_WRITER_PASSWORD"])
    args.extend([container_id, "sh", "-c", _WRITER_CMD if writer else _ROOT_CMD])
    try:
        result = subprocess.run(
            args, input=sql, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("AUDIT_LAB_EXECUTION_FAILED") from exc
    if deny:
        if result.returncode == 0 or not re.search(
            r"ERROR (?:1044|1045|1142|1143|1227) \(", result.stderr
        ):
            raise PreflightError("EXPECTED_ACCESS_DENIAL_NOT_OBSERVED")
        return ""
    if result.returncode:
        raise PreflightError("AUDIT_LAB_QUERY_FAILED")
    return result.stdout.strip()


def _integrity(container_id: str) -> bool:
    """Verify exact table set, all three trigger events, owner, body and engine."""
    rows = _sql(
        container_id,
        """
SELECT EVENT_OBJECT_TABLE, EVENT_MANIPULATION, DEFINER, ACTION_STATEMENT
FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='fitness_eval'
ORDER BY EVENT_OBJECT_TABLE, EVENT_MANIPULATION;
""",
    ).splitlines()
    expected = set()
    for table in _TABLES:
        for event in _EVENTS:
            body = f"INSERT INTO fitness_eval_audit.events (table_name, op) VALUES ('{table}', '{event}')"
            expected.add(f"{table}\t{event}\troot@localhost\t{body}")
    if set(rows) != expected or len(rows) != len(expected):
        return False
    table_list = ",".join(f"'{table}'" for table in _TABLES)
    counts = _sql(
        container_id,
        f"""
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema='fitness_eval' AND table_name IN ({table_list}) AND engine='InnoDB';
SELECT COUNT(*) FROM information_schema.referential_constraints
WHERE constraint_schema='fitness_eval' AND (UPDATE_RULE NOT IN ('RESTRICT','NO ACTION') OR DELETE_RULE NOT IN ('RESTRICT','NO ACTION'));
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema='fitness_eval_audit' AND table_name='events' AND engine='InnoDB';
""",
    ).splitlines()
    return counts == [str(len(_TABLES)), "0", "1"]


def _operations() -> str:
    """Each listed real schema table receives a synthetic INSERT/UPDATE/DELETE."""
    return """
INSERT INTO fitness_eval.contract(id, organization_id, user_id, course_id, remaining_class_hours) VALUES ('lab-contract','org-lab','student-lab','course-lab',5);
UPDATE fitness_eval.contract SET remaining_class_hours=4 WHERE id='lab-contract';
DELETE FROM fitness_eval.contract WHERE id='lab-contract';
INSERT INTO fitness_eval.appointment(id, organization_id, user_id, coach_id, contract_id, course_id, status, deleted) VALUES ('lab-appointment','org-lab','student-lab','coach-lab','base-contract','course-lab',1,0);
UPDATE fitness_eval.appointment SET status=2 WHERE id='lab-appointment';
DELETE FROM fitness_eval.appointment WHERE id='lab-appointment';
INSERT INTO fitness_eval.agent_booking_operation(request_id,appointment_id,organization_id,actor_id) VALUES ('lab-op','ap','org-lab','actor');
UPDATE fitness_eval.agent_booking_operation SET actor_id='changed' WHERE request_id='lab-op';
DELETE FROM fitness_eval.agent_booking_operation WHERE request_id='lab-op';
INSERT INTO fitness_eval.agent_booking_confirmation_consumption(jti,confirmation_id,tool_id,action,organization_id,resource,request_id,payload_hash) VALUES ('lab-jti','cid','tool','act','org-lab','resource','lab-consume',REPEAT('a',64));
UPDATE fitness_eval.agent_booking_confirmation_consumption SET action='changed' WHERE jti='lab-jti';
DELETE FROM fitness_eval.agent_booking_confirmation_consumption WHERE jti='lab-jti';
INSERT INTO fitness_eval.agent_booking_audit(appointment_id,organization_id,action,actor_id,request_id) VALUES ('ap','org-lab','create','actor','lab-audit');
UPDATE fitness_eval.agent_booking_audit SET action='changed' WHERE request_id='lab-audit';
DELETE FROM fitness_eval.agent_booking_audit WHERE request_id='lab-audit';
INSERT INTO fitness_eval.agent_booking_outbox(event_key,event_type,aggregate_id,organization_id,payload) VALUES ('lab-outbox','create','ap','org-lab',JSON_OBJECT());
UPDATE fitness_eval.agent_booking_outbox SET status='PUBLISHED' WHERE event_key='lab-outbox';
DELETE FROM fitness_eval.agent_booking_outbox WHERE event_key='lab-outbox';
INSERT INTO fitness_eval.agent_booking_resource_lock(organization_id,resource_type,resource_id,resource_date) VALUES ('org-lab','COACH','coach-lab','2030-01-07');
UPDATE fitness_eval.agent_booking_resource_lock SET resource_date='2030-01-08' WHERE resource_id='coach-lab';
DELETE FROM fitness_eval.agent_booking_resource_lock WHERE resource_id='coach-lab';
INSERT INTO fitness_eval.agent_booking_reschedule_operation(request_id,appointment_id,organization_id,actor_id) VALUES ('lab-reschedule','ap','org-lab','actor');
UPDATE fitness_eval.agent_booking_reschedule_operation SET actor_id='changed' WHERE request_id='lab-reschedule';
DELETE FROM fitness_eval.agent_booking_reschedule_operation WHERE request_id='lab-reschedule';
INSERT INTO fitness_eval.agent_booking_cancel_operation(request_id,appointment_id,organization_id,actor_id) VALUES ('lab-cancel','ap','org-lab','actor');
UPDATE fitness_eval.agent_booking_cancel_operation SET actor_id='changed' WHERE request_id='lab-cancel';
DELETE FROM fitness_eval.agent_booking_cancel_operation WHERE request_id='lab-cancel';
INSERT INTO fitness_eval.agent_booking_outbox_replay_audit(outbox_id,operator_id,reason) VALUES (1,'actor','test');
UPDATE fitness_eval.agent_booking_outbox_replay_audit SET reason='changed' WHERE outbox_id=1;
DELETE FROM fitness_eval.agent_booking_outbox_replay_audit WHERE outbox_id=1;
"""


def run(*, run_id: str, container_id: str, port: int) -> dict[str, Any]:
    docker_host = os.environ.get("DOCKER_HOST", "")
    if (
        not docker_host.startswith("unix:///")
        or not Path(docker_host.removeprefix("unix://")).is_socket()
        or os.environ.get("DOCKER_CONTEXT")
    ):
        raise PreflightError("LOCAL_DOCKER_SOCKET_REQUIRED")
    check_mysql(run_id=run_id, container_id=container_id, port=port)
    # Recheck the immutable container identity before any setup or account creation.
    verify_container(_inspect(container_id), run_id=run_id)
    fixture = (
        _ROOT / "fitness-core-gateway/src/test/resources/mcp/mysql-legacy-fixture.sql"
    ).read_text()
    migrations = _ROOT / "fitness-booking-service/src/main/resources/db/migration"
    schema = "".join(
        (migrations / name).read_text()
        for name in (
            "V20260815_001__create_booking_agent_tables.sql",
            "V20260815_002__create_booking_operation_tables.sql",
            "V20260905_003__create_outbox_replay_audit.sql",
        )
    )
    _sql(container_id, "USE fitness_eval;\n" + fixture + "\n" + schema)
    _sql(
        container_id,
        """
INSERT INTO fitness_eval.organization(id,name) VALUES ('org-lab','synthetic');
INSERT INTO fitness_eval.login_user(id,name) VALUES ('student-lab','synthetic'),('coach-lab','synthetic');
INSERT INTO fitness_eval.course(id,name,organization_id) VALUES ('course-lab','synthetic','org-lab');
INSERT INTO fitness_eval.contract(id,organization_id,user_id,course_id,remaining_class_hours) VALUES ('base-contract','org-lab','student-lab','course-lab',5);
CREATE DATABASE fitness_eval_audit;
CREATE TABLE fitness_eval_audit.events (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, table_name VARCHAR(128) NOT NULL, op VARCHAR(6) NOT NULL) ENGINE=InnoDB;
""",
    )
    password = secrets.token_hex(32)
    os.environ["TASK_EVAL_AUDIT_WRITER_PASSWORD"] = password
    _sql(
        container_id,
        f"CREATE USER 'eval_writer'@'localhost' IDENTIFIED BY '{password}'; GRANT SELECT ON fitness_eval.* TO 'eval_writer'@'localhost';",
    )
    for table in _TABLES:
        _sql(
            container_id,
            f"GRANT INSERT, UPDATE, DELETE ON fitness_eval.`{table}` TO 'eval_writer'@'localhost';",
        )
        for event in _EVENTS:
            trigger = f"audit_{table}_{event.lower()}"
            _sql(
                container_id,
                f"CREATE DEFINER=CURRENT_USER TRIGGER fitness_eval.`{trigger}` AFTER {event} ON fitness_eval.`{table}` FOR EACH ROW INSERT INTO fitness_eval_audit.events (table_name, op) VALUES ('{table}', '{event}');",
            )
    if not _integrity(container_id):
        raise PreflightError("AUDIT_INTEGRITY_FAILED")
    if _sql(container_id, "SELECT CURRENT_USER();", writer=True) != "eval_writer@localhost":
        raise PreflightError("WRITER_IDENTITY_MISMATCH")
    for forbidden in (
        "DELETE FROM fitness_eval_audit.events;",
        "SELECT * FROM fitness_eval_audit.events;",
        "INSERT INTO fitness_eval.course(id,name) VALUES ('unauthorized','synthetic');",
        "DROP TRIGGER fitness_eval.audit_contract_insert;",
        "ALTER TABLE fitness_eval.contract ADD COLUMN unwanted INT;",
        "TRUNCATE TABLE fitness_eval.contract;",
    ):
        _sql(container_id, forbidden, writer=True, deny=True)
    _sql(container_id, "START TRANSACTION;\n" + _operations() + "\nROLLBACK;", writer=True)
    if _sql(container_id, "SELECT COUNT(*) FROM fitness_eval_audit.events;") != "0":
        raise PreflightError("ROLLBACK_AUDIT_MISMATCH")
    _sql(container_id, "START TRANSACTION;\n" + _operations() + "\nCOMMIT;", writer=True)
    observed = _sql(
        container_id,
        "SELECT table_name, op, COUNT(*) FROM fitness_eval_audit.events GROUP BY table_name, op;",
    ).splitlines()
    expected = {f"{table}\t{event}\t1" for table in _TABLES for event in _EVENTS}
    if len(observed) != len(expected) or set(observed) != expected or not _integrity(container_id):
        raise PreflightError("COMMITTED_AUDIT_MISMATCH")
    # Deliberately mutate only this throwaway DB as a negative integrity control.
    _sql(container_id, "DROP TRIGGER fitness_eval.audit_contract_insert;")
    if _integrity(container_id):
        raise PreflightError("MISSING_TRIGGER_NOT_DETECTED")
    return {
        "status": "PASS",
        "origin": "isolated_mysql_restricted_user_trigger_lab",
        "covered_tables": list(_TABLES),
        "observed_committed_event_kinds": len(observed),
        "rolled_back_event_count": 0,
        "unauthorized_actions_denied": 6,
        "pre_post_integrity_verified": True,
        "missing_trigger_detected": True,
        "limitations": [
            "Only committed row mutations in the listed tables and this test database are covered.",
            "Triggers and audit table are in the same MySQL server and transaction, not external tamper-proof storage.",
            "No bounded audit window or concurrent transaction ordering has been proven.",
            "No Gateway, Booking service, actual task, PostgreSQL causal ordering, or production authorization is verified.",
        ],
        "task_status": "UNKNOWN",
        "cross_database_causality": "UNKNOWN",
    }


def main() -> int:
    try:
        port = int(os.environ.get("TASK_EVAL_MYSQL_PORT", ""))
        result = run(
            run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
            container_id=os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", ""),
            port=port,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:  # noqa: BLE001 - never print database errors, SQL, or credentials
        print(json.dumps({"status": "ERROR", "reason": "ISOLATION_OR_AUDIT_LAB_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
