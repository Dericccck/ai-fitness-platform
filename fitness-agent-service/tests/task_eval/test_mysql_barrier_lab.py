import json

import pytest

from app.evaluation.task_eval import mysql_barrier_lab as lab


def test_run_exposes_only_fixed_failure_stage(monkeypatch):
    def fail(*, state, **_kwargs):
        state["stage"] = "ACQUIRE_LOCK"
        raise RuntimeError("private password and raw SQL")

    monkeypatch.setattr(lab, "_run_impl", fail)
    with pytest.raises(lab.BarrierExperimentError) as error:
        lab.run(run_id="synthetic", container_id="synthetic", port=1)
    assert error.value.stage == "ACQUIRE_LOCK"
    assert "private" not in str(error.value)


def test_cli_does_not_expose_raw_exception(monkeypatch, capsys):
    monkeypatch.setenv("TASK_EVAL_MYSQL_PORT", "1")

    def fail(**_kwargs):
        raise lab.BarrierExperimentError("CHECK_INFLIGHT")

    monkeypatch.setattr(lab, "run", fail)
    assert lab.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "ERROR",
        "reason": "BARRIER_EXPERIMENT_FAILED",
        "stage": "CHECK_INFLIGHT",
    }


def test_unknown_stage_is_rejected():
    assert lab.BarrierExperimentError("private SQL").stage == "PREFLIGHT"


def test_known_transaction_requires_three_consecutive_absent_observations(monkeypatch):
    readings = iter(["0", "1", "0", "0", "0"])
    seen = []

    def sql(*_args):
        value = next(readings)
        seen.append(value)
        return value

    monkeypatch.setattr(lab, "_sql", sql)
    monkeypatch.setattr(lab.time, "sleep", lambda _seconds: None)
    lab._wait_known_transaction_absent("container", "query")
    assert seen == ["0", "1", "0", "0", "0"]


def test_persistent_or_invalid_transaction_state_cannot_pass(monkeypatch):
    monkeypatch.setattr(lab, "_sql", lambda *_args: "1")
    with pytest.raises(lab.PreflightError, match="FIRST_TRANSACTION_NOT_DRAINED"):
        lab._wait_known_transaction_absent("container", "query", timeout=0.001, interval=0.0001)
    monkeypatch.setattr(lab, "_sql", lambda *_args: "unexpected")
    with pytest.raises(lab.PreflightError, match="INVALID_TRANSACTION_OBSERVATION"):
        lab._wait_known_transaction_absent("container", "query")
