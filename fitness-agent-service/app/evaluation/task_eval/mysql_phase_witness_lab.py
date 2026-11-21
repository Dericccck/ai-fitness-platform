"""Observe write-phase markers in a launcher-owned disposable MySQL.

This synthetic experiment is not an Agent run or proof of a complete audit.
It checks whether a transition waits for a previous write transaction, whether
new writes wait at the transition, and whether rollback removes audit records.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from .mysql_audit_lab import _sql
from .mysql_barrier_lab import _close, _line, _send, _spawn, _wait
from .mysql_sandbox_preflight import check_mysql
from .sandbox_preflight import PreflightError


def _check_results(rows: str) -> None:
    if rows.splitlines() != ["1\tPENDING", "2\tAPPROVED"]:
        raise PreflightError("PHASE_WITNESS_ROWS_MISMATCH")


def _session_id(proc: Any, marker: str) -> int:
    _send(proc, f"SELECT CONCAT('{marker}:',CONNECTION_ID());")
    line = _line(proc.stdout, 10)
    match = re.fullmatch(re.escape(marker) + r":([1-9][0-9]*)", line or "")
    if not match:
        raise PreflightError("PHASE_SESSION_ID_UNAVAILABLE")
    return int(match.group(1))


def _wait_guard_lock(container_id: str, session_id: int, timeout: float = 10) -> None:
    """Verify MySQL reports this exact connection waiting on the guard row."""
    if type(session_id) is not int or session_id <= 0:
        raise PreflightError("PHASE_SESSION_ID_INVALID")
    query = f"""
SELECT COUNT(*) FROM performance_schema.data_lock_waits w
JOIN performance_schema.threads t ON t.THREAD_ID=w.REQUESTING_THREAD_ID
JOIN performance_schema.data_locks l ON l.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID
 AND l.ENGINE=w.ENGINE
WHERE t.PROCESSLIST_ID={session_id} AND l.OBJECT_SCHEMA='fitness_eval_audit'
 AND l.OBJECT_NAME='phase' AND l.LOCK_TYPE='RECORD';
"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = _sql(container_id, query)
        if count == "1":
            return
        if count != "0":
            raise PreflightError("PHASE_LOCK_OBSERVATION_INVALID")
        time.sleep(0.1)
    raise PreflightError("PHASE_LOCK_WAIT_NOT_OBSERVED")


def _run_impl(
    *, run_id: str, container_id: str, port: int, state: dict[str, str]
) -> dict[str, Any]:
    host = os.environ.get("DOCKER_HOST", "")
    if (
        not host.startswith("unix:///")
        or not Path(host.removeprefix("unix://")).is_socket()
        or os.environ.get("DOCKER_CONTEXT")
    ):
        raise PreflightError("LOCAL_DOCKER_SOCKET_REQUIRED")
    check_mysql(run_id=run_id, container_id=container_id, port=port)
    state["stage"] = "SETUP"
    password = secrets.token_hex(32)
    os.environ["TASK_EVAL_AUDIT_WRITER_PASSWORD"] = password
    _sql(
        container_id,
        f"""
CREATE DATABASE fitness_eval_audit;
CREATE TABLE fitness_eval_audit.phase (id INT PRIMARY KEY, phase VARCHAR(16) NOT NULL) ENGINE=InnoDB;
INSERT INTO fitness_eval_audit.phase VALUES (1,'PENDING');
CREATE TABLE fitness_eval_audit.events (probe_id INT NOT NULL, phase_at_write VARCHAR(16) NOT NULL) ENGINE=InnoDB;
CREATE TABLE fitness_eval.phase_probe (id INT PRIMARY KEY) ENGINE=InnoDB;
CREATE USER 'eval_audit_owner'@'localhost' ACCOUNT LOCK;
GRANT SELECT ON fitness_eval_audit.phase TO 'eval_audit_owner'@'localhost';
GRANT INSERT ON fitness_eval_audit.events TO 'eval_audit_owner'@'localhost';
GRANT TRIGGER ON fitness_eval.phase_probe TO 'eval_audit_owner'@'localhost';
GRANT SELECT ON fitness_eval.phase_probe TO 'eval_audit_owner'@'localhost';
CREATE USER 'eval_writer'@'localhost' IDENTIFIED BY '{password}';
GRANT INSERT, SELECT ON fitness_eval.phase_probe TO 'eval_writer'@'localhost';
CREATE DEFINER='eval_audit_owner'@'localhost' TRIGGER fitness_eval.phase_probe_insert
AFTER INSERT ON fitness_eval.phase_probe FOR EACH ROW
INSERT INTO fitness_eval_audit.events (probe_id, phase_at_write)
VALUES (NEW.id, (SELECT phase FROM fitness_eval_audit.phase WHERE id=1));
""",
    )
    state["stage"] = "DENIAL"
    _sql(
        container_id,
        "UPDATE fitness_eval_audit.phase SET phase='APPROVED' WHERE id=1;",
        writer=True,
        deny=True,
    )
    first = second = transition = None
    try:
        state["stage"] = "WRITE_PENDING"
        first = _spawn(container_id, writer=True)
        _send(
            first,
            "START TRANSACTION; INSERT INTO fitness_eval.phase_probe VALUES(1); SELECT 'OPEN';",
        )
        _wait(first.stdout, "OPEN")
        if _sql(container_id, "SELECT COUNT(*) FROM fitness_eval_audit.events;") != "0":
            raise PreflightError("UNCOMMITTED_AUDIT_VISIBLE")
        state["stage"] = "TRANSITION"
        transition = _spawn(container_id, writer=False)
        transition_id = _session_id(transition, "TRANSITION_READY")
        _send(
            transition,
            "UPDATE fitness_eval_audit.phase SET phase='APPROVED' WHERE id=1; SELECT 'TRANSITIONED';",
        )
        _wait_guard_lock(container_id, transition_id)
        if _line(transition.stdout, 0.1) is not None:
            raise PreflightError("TRANSITION_DID_NOT_WAIT")
        second = _spawn(container_id, writer=True)
        second_id = _session_id(second, "SECOND_READY")
        _send(second, "INSERT INTO fitness_eval.phase_probe VALUES(2); SELECT 'SECOND';")
        _wait_guard_lock(container_id, second_id)
        if _line(second.stdout, 0.1) is not None:
            raise PreflightError("LATER_WRITER_DID_NOT_WAIT")
        state["stage"] = "COMMIT_FIRST"
        _send(first, "COMMIT; SELECT 'COMMITTED';")
        _wait(first.stdout, "COMMITTED")
        _wait(transition.stdout, "TRANSITIONED")
        state["stage"] = "WRITE_APPROVED"
        _wait(second.stdout, "SECOND")
        _send(
            second,
            "START TRANSACTION; INSERT INTO fitness_eval.phase_probe VALUES(3); ROLLBACK; SELECT 'ROLLED_BACK';",
        )
        _wait(second.stdout, "ROLLED_BACK")
        state["stage"] = "CHECK_ROWS"
        _check_results(
            _sql(
                container_id,
                "SELECT probe_id,phase_at_write FROM fitness_eval_audit.events ORDER BY probe_id;",
            )
        )
        return {
            "status": "PASS",
            "origin": "disposable_mysql_synthetic_phase_witness",
            "transition_waited_for_pre_transition_write": True,
            "later_writer_waited_for_transition": True,
            "pre_transition_write_marked_pending": True,
            "post_transition_write_marked_approved": True,
            "rolled_back_write_absent": True,
            "restricted_writer_cannot_update_phase": True,
            "task_status": "UNKNOWN",
            "audit_window_complete": False,
            "cross_database_approval_causality": "UNKNOWN",
            "transaction_settlement_proven": False,
            "limitations": [
                "Synthetic experiment: no Agent, PostgreSQL approval, Gateway or Booking service.",
                "The experiment does not prove all writers are covered or later transactions have drained.",
                "The phase marker is set by the local test administrator and is not independently attested.",
            ],
        }
    finally:
        existing = sys.exc_info()[0] is not None
        failed = False
        for proc in (second, transition, first):
            try:
                _close(proc)
            except Exception:  # noqa: BLE001
                failed = True
        if failed and not existing:
            raise PreflightError("PHASE_WITNESS_CLEANUP_FAILED")


class PhaseWitnessError(PreflightError):
    def __init__(self, stage: str) -> None:
        self.stage = (
            stage
            if stage
            in {
                "PREFLIGHT",
                "SETUP",
                "DENIAL",
                "WRITE_PENDING",
                "TRANSITION",
                "COMMIT_FIRST",
                "WRITE_APPROVED",
                "CHECK_ROWS",
            }
            else "PREFLIGHT"
        )
        super().__init__("PHASE_WITNESS_EXPERIMENT_FAILED")


def run(*, run_id: str, container_id: str, port: int) -> dict[str, Any]:
    state = {"stage": "PREFLIGHT"}
    try:
        return _run_impl(run_id=run_id, container_id=container_id, port=port, state=state)
    except Exception as exc:
        raise PhaseWitnessError(state["stage"]) from exc


def main() -> int:
    try:
        result = run(
            run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
            container_id=os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", ""),
            port=int(os.environ.get("TASK_EVAL_MYSQL_PORT", "")),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - do not expose credentials or SQL
        stage = exc.stage if isinstance(exc, PhaseWitnessError) else "PREFLIGHT"
        print(
            json.dumps(
                {"status": "ERROR", "reason": "PHASE_WITNESS_EXPERIMENT_FAILED", "stage": stage}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
