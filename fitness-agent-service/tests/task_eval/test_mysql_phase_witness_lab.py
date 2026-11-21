import json

import pytest

from app.evaluation.task_eval import mysql_phase_witness_lab as lab
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def test_phase_rows_require_exact_late_commit_and_no_rollback_record():
    lab._check_results("1\tPENDING\n2\tAPPROVED")
    for rows in ("1\tAPPROVED\n2\tAPPROVED", "1\tPENDING", "1\tPENDING\n2\tAPPROVED\n3\tAPPROVED"):
        with pytest.raises(PreflightError):
            lab._check_results(rows)


def test_cli_sanitizes_errors(monkeypatch, capsys):
    monkeypatch.setenv("TASK_EVAL_MYSQL_PORT", "3306")

    def fail(**_kwargs):
        raise RuntimeError("password or private SQL")

    monkeypatch.setattr(lab, "run", fail)
    assert lab.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "ERROR",
        "reason": "PHASE_WITNESS_EXPERIMENT_FAILED",
        "stage": "PREFLIGHT",
    }


def test_lock_wait_requires_mysql_observation_for_known_session(monkeypatch):
    readings = iter(["0", "0", "1"])
    commands = []

    def sql(_container, query):
        commands.append(query)
        return next(readings)

    monkeypatch.setattr(lab, "_sql", sql)
    monkeypatch.setattr(lab.time, "sleep", lambda _: None)
    lab._wait_guard_lock("container", 123)
    assert len(commands) == 3
    assert all(
        "PROCESSLIST_ID=123" in command and "data_lock_waits" in command for command in commands
    )


@pytest.mark.parametrize("value", [True, 0, -1, "123"])
def test_invalid_lock_session_id_is_rejected(value):
    with pytest.raises(PreflightError, match="PHASE_SESSION_ID_INVALID"):
        lab._wait_guard_lock("container", value)
