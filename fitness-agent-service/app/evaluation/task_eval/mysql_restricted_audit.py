"""Restricted accounts and separate-owner audit for the disposable booking trial.

Only a verified launcher-owned temporary MySQL is accepted. Trigger rows are
transactional local observations, not tamper-proof evidence or a proven window.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .mysql_audit_lab import _EVENTS, _ROOT, _TABLES, _sql
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect

_BOOKING = "eval_booking"
_GATEWAY = "eval_gateway"
_OWNER = "eval_audit_owner"
_BODY = "INSERT INTO fitness_eval_audit.events (table_name, op) VALUES ('{table}', '{event}')"


def _container() -> tuple[str, str]:
    docker_host = os.environ.get("DOCKER_HOST", "")
    if (
        not docker_host.startswith("unix:///")
        or not Path(docker_host.removeprefix("unix://")).is_socket()
        or os.environ.get("DOCKER_CONTEXT")
    ):
        raise PreflightError("LOCAL_DOCKER_SOCKET_REQUIRED")
    run_id = os.environ.get("TASK_EVAL_RUN_ID", "")
    cid = os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", "")
    verify_container(_inspect(cid), run_id=run_id)
    if _sql(cid, "SELECT DATABASE(), CURRENT_USER();") != "NULL\troot@localhost":
        raise PreflightError("AUDIT_DB_IDENTITY_MISMATCH")
    return run_id, cid


def _client(cid: str, user: str, sql: str, *, deny: bool = False) -> str:
    env_name = (
        "TASK_EVAL_BOOKING_DB_PASSWORD" if user == _BOOKING else "TASK_EVAL_GATEWAY_DB_PASSWORD"
    )
    if user not in {_BOOKING, _GATEWAY} or not re.fullmatch(
        r"[0-9a-f]{48}", os.environ.get(env_name, "")
    ):
        raise PreflightError("RESTRICTED_CREDENTIAL_INVALID")
    command = f'MYSQL_PWD="${env_name}" exec mysql --protocol=tcp -h127.0.0.1 -u{user} --batch --raw --skip-column-names'
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", "-e", env_name, cid, "sh", "-c", command],
            input=sql,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("RESTRICTED_QUERY_FAILED") from exc
    if deny:
        if result.returncode == 0 or not re.search(
            r"ERROR (?:1044|1142|1143|1227) \(", result.stderr
        ):
            raise PreflightError("REQUIRED_DENIAL_MISSING")
        return ""
    if result.returncode:
        raise PreflightError("RESTRICTED_QUERY_FAILED")
    return result.stdout.strip()


def setup() -> None:
    _, cid = _container()
    if (
        _sql(
            cid,
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='fitness_eval_audit';",
        )
        != "0"
    ):
        raise PreflightError("AUDIT_SCHEMA_ALREADY_EXISTS")
    paths = _ROOT / "fitness-booking-service/src/main/resources/db/migration"
    schema = "".join(
        (paths / name).read_text()
        for name in (
            "V20260815_001__create_booking_agent_tables.sql",
            "V20260815_002__create_booking_operation_tables.sql",
            "V20260905_003__create_outbox_replay_audit.sql",
        )
    )
    # Match BookingDatabaseSchema's required outbox columns before disabling app DDL.
    _sql(
        cid,
        "USE fitness_eval;\n"
        + schema
        + """
ALTER TABLE agent_booking_outbox
 ADD COLUMN aggregate_version BIGINT NOT NULL DEFAULT 1 AFTER organization_id,
 ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 AFTER status,
 ADD COLUMN next_attempt_at TIMESTAMP NULL AFTER attempt_count,
 ADD COLUMN last_error VARCHAR(2000) NULL AFTER next_attempt_at,
 ADD COLUMN claimed_at TIMESTAMP NULL AFTER last_error,
 ADD COLUMN claimed_by VARCHAR(128) NULL AFTER claimed_at,
 ADD COLUMN replay_count INT NOT NULL DEFAULT 0 AFTER claimed_by,
 ADD COLUMN last_replayed_by VARCHAR(32) NULL AFTER replay_count,
 ADD COLUMN last_replayed_at TIMESTAMP NULL AFTER last_replayed_by;
CREATE INDEX idx_agent_booking_outbox_claim ON agent_booking_outbox (status,next_attempt_at,claimed_at,created_at);
CREATE DATABASE fitness_eval_audit;
CREATE TABLE fitness_eval_audit.events (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, table_name VARCHAR(128) NOT NULL, op VARCHAR(6) NOT NULL) ENGINE=InnoDB;
CREATE USER 'eval_audit_owner'@'localhost' ACCOUNT LOCK;
GRANT INSERT ON fitness_eval_audit.events TO 'eval_audit_owner'@'localhost';
""",
    )
    for user, env_name in (
        (_BOOKING, "TASK_EVAL_BOOKING_DB_PASSWORD"),
        (_GATEWAY, "TASK_EVAL_GATEWAY_DB_PASSWORD"),
    ):
        password = os.environ.get(env_name, "")
        if not re.fullmatch(r"[0-9a-f]{48}", password):
            raise PreflightError("RESTRICTED_CREDENTIAL_INVALID")
        _sql(
            cid,
            f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}'; GRANT SELECT ON fitness_eval.* TO '{user}'@'%';",
        )
    for table in _TABLES:
        _sql(
            cid,
            f"GRANT INSERT, UPDATE, DELETE ON fitness_eval.`{table}` TO 'eval_booking'@'%'; GRANT TRIGGER ON fitness_eval.`{table}` TO 'eval_audit_owner'@'localhost';",
        )
        for event in _EVENTS:
            trigger = f"audit_{table}_{event.lower()}"
            body = _BODY.format(table=table, event=event)
            _sql(
                cid,
                f"CREATE DEFINER='eval_audit_owner'@'localhost' TRIGGER fitness_eval.`{trigger}` AFTER {event} ON fitness_eval.`{table}` FOR EACH ROW {body};",
            )
    verify()


def verify() -> dict[str, Any]:
    _, cid = _container()
    rows = _sql(
        cid,
        "SELECT EVENT_OBJECT_TABLE,EVENT_MANIPULATION,DEFINER,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='fitness_eval';",
    ).splitlines()
    expected = {
        f"{table}\t{event}\t{_OWNER}@localhost\t" + _BODY.format(table=table, event=event)
        for table in _TABLES
        for event in _EVENTS
    }
    if len(rows) != len(expected) or set(rows) != expected:
        raise PreflightError("RESTRICTED_TRIGGER_INTEGRITY_FAILED")
    names = ",".join(f"'{name}'" for name in _TABLES)
    checks = _sql(
        cid,
        f"""
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='fitness_eval' AND table_name IN ({names}) AND engine='InnoDB';
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='fitness_eval_audit' AND table_name='events' AND engine='InnoDB';
SELECT COUNT(*) FROM information_schema.referential_constraints WHERE constraint_schema='fitness_eval' AND (UPDATE_RULE NOT IN ('RESTRICT','NO ACTION') OR DELETE_RULE NOT IN ('RESTRICT','NO ACTION'));
SELECT account_locked FROM mysql.user WHERE user='eval_audit_owner' AND host='localhost';
""",
    ).splitlines()
    if checks != [str(len(_TABLES)), "1", "0", "Y"]:
        raise PreflightError("RESTRICTED_AUDIT_SCHEMA_INVALID")
    owner_grants = set(_sql(cid, "SHOW GRANTS FOR 'eval_audit_owner'@'localhost';").splitlines())
    expected_owner = {
        "GRANT USAGE ON *.* TO `eval_audit_owner`@`localhost`",
        "GRANT INSERT ON `fitness_eval_audit`.`events` TO `eval_audit_owner`@`localhost`",
    } | {
        f"GRANT TRIGGER ON `fitness_eval`.`{table}` TO `eval_audit_owner`@`localhost`"
        for table in _TABLES
    }
    if owner_grants != expected_owner:
        raise PreflightError("AUDIT_OWNER_GRANTS_MISMATCH")
    # Exact grants disallow global, database-wide DML, DDL, auditing and roles.
    for user in (_BOOKING, _GATEWAY):
        grants = set(_sql(cid, f"SHOW GRANTS FOR '{user}'@'%';").splitlines())
        required = {
            f"GRANT USAGE ON *.* TO `{user}`@`%`",
            f"GRANT SELECT ON `fitness_eval`.* TO `{user}`@`%`",
        }
        if user == _BOOKING:
            required |= {
                f"GRANT INSERT, UPDATE, DELETE ON `fitness_eval`.`{table}` TO `{user}`@`%`"
                for table in _TABLES
            }
        if grants != required:
            raise PreflightError("RESTRICTED_GRANTS_MISMATCH")
        if _client(cid, user, "SELECT CURRENT_USER();") != f"{user}@%":
            raise PreflightError("RESTRICTED_IDENTITY_MISMATCH")
        _client(cid, user, "SELECT * FROM fitness_eval_audit.events;", deny=True)
        _client(cid, user, "DROP TRIGGER fitness_eval.audit_contract_insert;", deny=True)
        _client(cid, user, "ALTER TABLE fitness_eval.contract ADD COLUMN unwanted INT;", deny=True)
    _client(
        cid,
        _BOOKING,
        "INSERT INTO fitness_eval.course(id,name) VALUES ('unauthorized','test');",
        deny=True,
    )
    _client(cid, _GATEWAY, "UPDATE fitness_eval.contract SET remaining_class_hours=0;", deny=True)
    return {
        "status": "VERIFIED",
        "tables": list(_TABLES),
        "trigger_count": len(expected),
        "booking_account": "restricted_dml",
        "gateway_account": "read_only",
        "audit_owner": "locked_account",
        "independent_provenance": False,
    }


def observe() -> dict[str, Any]:
    """Return transactional audit counts, without claiming a closed commit window."""
    _, cid = _container()
    output = _sql(
        cid,
        "SELECT table_name,op,COUNT(*) FROM fitness_eval_audit.events GROUP BY table_name,op ORDER BY table_name,op;",
    )
    records = []
    for line in output.splitlines():
        values = line.split("\t")
        if (
            len(values) != 3
            or values[0] not in _TABLES
            or values[1] not in _EVENTS
            or not values[2].isdigit()
        ):
            raise PreflightError("INVALID_AUDIT_OBSERVATION")
        records.append({"table": values[0], "operation": values[1], "count": int(values[2])})
    return {
        "records": records,
        "audit_window_complete": False,
        "cross_database_causality": "UNKNOWN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("setup", "verify"))
    args = parser.parse_args()
    try:
        if args.operation == "setup":
            setup()
            result = {"status": "VERIFIED"}
        else:
            result = verify()
        print(json.dumps(result))
        return 0
    except Exception:  # noqa: BLE001 - do not expose SQL, settings or credentials
        print(json.dumps({"status": "ERROR", "reason": "RESTRICTED_AUDIT_SETUP_OR_VERIFY_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
