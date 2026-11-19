"""Disposable-only experiment for a MySQL global read lock and in-flight writers.

The experiment has no application or PostgreSQL connection. It must never be
interpreted as a verified Agent task window or a release decision.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

from .mysql_audit_lab import _ROOT_CMD, _WRITER_CMD, _sql
from .mysql_sandbox_preflight import check_mysql
from .sandbox_preflight import PreflightError


def _spawn(cid: str, *, writer: bool) -> subprocess.Popen[str]:
    args = ["docker", "exec", "-i"]
    if writer:
        args += ["-e", "TASK_EVAL_AUDIT_WRITER_PASSWORD"]
    args += [cid, "sh", "-c", (_WRITER_CMD if writer else _ROOT_CMD) + " --unbuffered"]
    return subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def _send(proc: subprocess.Popen[str], sql: str) -> None:
    if proc.stdin is None or proc.poll() is not None:
        raise PreflightError("BARRIER_SESSION_CLOSED")
    proc.stdin.write(sql + "\n")
    proc.stdin.flush()


def _line(stream: IO[str] | None, timeout: float) -> str | None:
    if stream is None:
        raise PreflightError("BARRIER_SESSION_UNAVAILABLE")
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        if not selector.select(timeout):
            return None
        value = stream.readline().strip()
        if not value:
            raise PreflightError("BARRIER_SESSION_CLOSED")
        return value


def _wait(stream: IO[str] | None, marker: str, timeout: float = 10) -> None:
    if _line(stream, timeout) != marker:
        raise PreflightError("BARRIER_EXPECTED_MARKER_MISSING")


def _close(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    try:
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.close()
        proc.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        proc.kill()
        proc.wait(timeout=2)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()


def _wait_known_transaction_absent(
    cid: str, query: str, *, timeout: float = 5, interval: float = 0.2
) -> None:
    """Bound stale InnoDB metadata; absence must be observed three times.

    This checks only the known test connection and never proves all writers
    or cross-database causal ordering.
    """
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        value = _sql(cid, query)
        if value not in {"0", "1"}:
            raise PreflightError("INVALID_TRANSACTION_OBSERVATION")
        consecutive = consecutive + 1 if value == "0" else 0
        if consecutive >= 3:
            return
        time.sleep(interval)
    raise PreflightError("FIRST_TRANSACTION_NOT_DRAINED")


_STAGES = frozenset(
    {
        "PREFLIGHT",
        "SETUP",
        "START_WRITER",
        "CHECK_INFLIGHT",
        "ACQUIRE_LOCK",
        "COMPLETE_FIRST_TRANSACTION",
        "VERIFY_DRAIN",
        "CHECK_BLOCKED_WRITER",
        "RELEASE_LOCK",
        "CHECK_RELEASED_WRITE",
        "CLEANUP",
    }
)


class BarrierExperimentError(PreflightError):
    def __init__(self, stage: str) -> None:
        self.stage = stage if stage in _STAGES else "PREFLIGHT"
        super().__init__("BARRIER_EXPERIMENT_FAILED")


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
CREATE TABLE fitness_eval.barrier_probe(id INT PRIMARY KEY) ENGINE=InnoDB;
CREATE USER 'eval_writer'@'localhost' IDENTIFIED BY '{password}';
GRANT INSERT, SELECT ON fitness_eval.barrier_probe TO 'eval_writer'@'localhost';
""",
    )
    first = lock = second = None
    try:
        state["stage"] = "START_WRITER"
        first = _spawn(container_id, writer=True)
        _send(
            first,
            "START TRANSACTION; INSERT INTO fitness_eval.barrier_probe VALUES(1); SELECT CONCAT('WRITER_OPEN:',CONNECTION_ID());",
        )
        opening = _line(first.stdout, 10)
        match = re.fullmatch(r"WRITER_OPEN:([0-9]+)", opening or "")
        if not match:
            raise PreflightError("ACTIVE_WRITER_NOT_STARTED")
        thread_id = int(match.group(1))
        state["stage"] = "CHECK_INFLIGHT"
        trx_query = f"SELECT COUNT(*) FROM information_schema.innodb_trx WHERE trx_mysql_thread_id={thread_id};"
        if _sql(container_id, trx_query) != "1":
            raise PreflightError("ACTIVE_TRANSACTION_NOT_OBSERVED")
        state["stage"] = "ACQUIRE_LOCK"
        lock = _spawn(container_id, writer=False)
        _send(lock, "FLUSH TABLES WITH READ LOCK; SELECT 'LOCK_ACQUIRED';")
        early = _line(lock.stdout, 1)
        observed_pending = _sql(container_id, trx_query) == "1"
        if not observed_pending:
            raise PreflightError("ACTIVE_TRANSACTION_DISAPPEARED")
        state["stage"] = "COMPLETE_FIRST_TRANSACTION"
        if early == "LOCK_ACQUIRED":
            # Read-lock acquisition alone was not enough; do not call it drained.
            _send(lock, "UNLOCK TABLES; SELECT 'LOCK_RELEASED';")
            _wait(lock.stdout, "LOCK_RELEASED")
            _send(first, "COMMIT; SELECT 'FIRST_COMMITTED';")
            _wait(first.stdout, "FIRST_COMMITTED")
            _send(lock, "FLUSH TABLES WITH READ LOCK; SELECT 'LOCK_ACQUIRED';")
            _wait(lock.stdout, "LOCK_ACQUIRED")
            acquired_with_inflight = True
        elif early is None:
            _send(first, "COMMIT; SELECT 'FIRST_COMMITTED';")
            _wait(first.stdout, "FIRST_COMMITTED")
            _wait(lock.stdout, "LOCK_ACQUIRED")
            acquired_with_inflight = False
        else:
            raise PreflightError("UNEXPECTED_BARRIER_RESPONSE")
        state["stage"] = "VERIFY_DRAIN"
        _wait_known_transaction_absent(container_id, trx_query)
        state["stage"] = "CHECK_BLOCKED_WRITER"
        second = _spawn(container_id, writer=True)
        _send(
            second, "INSERT INTO fitness_eval.barrier_probe VALUES(2); SELECT 'SECOND_COMMITTED';"
        )
        if _line(second.stdout, 1) is not None:
            raise PreflightError("WRITE_NOT_BLOCKED")
        if _sql(container_id, "SELECT COUNT(*) FROM fitness_eval.barrier_probe;") != "1":
            raise PreflightError("BARRIER_READ_MISMATCH")
        state["stage"] = "RELEASE_LOCK"
        _send(lock, "UNLOCK TABLES; SELECT 'LOCK_RELEASED';")
        _wait(lock.stdout, "LOCK_RELEASED")
        _wait(second.stdout, "SECOND_COMMITTED")
        state["stage"] = "CHECK_RELEASED_WRITE"
        if _sql(container_id, "SELECT COUNT(*) FROM fitness_eval.barrier_probe;") != "2":
            raise PreflightError("POST_RELEASE_WRITE_MISSING")
        return {
            "status": "PASS",
            "origin": "disposable_mysql_barrier_mechanism_experiment",
            "inflight_transaction_observed_independently": True,
            "global_lock_acquired_while_inflight": acquired_with_inflight,
            "new_writer_blocked_while_locked": True,
            "new_writer_committed_after_unlock": True,
            "task_status": "UNKNOWN",
            "complete_pre_confirmation_window": False,
            "cross_database_causality": "UNKNOWN",
            "limitations": [
                "This is a synthetic MySQL experiment, not the Agent, Gateway, Booking, or the development case.",
                "One observed transaction and blocked writer do not prove all writers are controlled or all in-flight transactions are drained.",
                "An unlocked gap before PostgreSQL approval and cross-database causal order are not covered.",
            ],
        }
    finally:
        existing_error = sys.exc_info()[0] is not None
        if not existing_error:
            state["stage"] = "CLEANUP"
        cleanup_failed = False
        for proc in (second, lock, first):
            try:
                _close(proc)
            except Exception:  # noqa: BLE001 - try every session before reporting cleanup failure
                cleanup_failed = True
        if cleanup_failed and not existing_error:
            raise PreflightError("BARRIER_CLEANUP_FAILED")


def run(*, run_id: str, container_id: str, port: int) -> dict[str, Any]:
    state = {"stage": "PREFLIGHT"}
    try:
        return _run_impl(run_id=run_id, container_id=container_id, port=port, state=state)
    except Exception as exc:
        raise BarrierExperimentError(state["stage"]) from exc


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
        stage = exc.stage if isinstance(exc, BarrierExperimentError) else "PREFLIGHT"
        print(
            json.dumps({"status": "ERROR", "reason": "BARRIER_EXPERIMENT_FAILED", "stage": stage})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
