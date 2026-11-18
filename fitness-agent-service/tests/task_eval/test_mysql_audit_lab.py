from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import mysql_audit_lab as lab
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def trigger_rows():
    return [
        f"{table}\t{event}\troot@localhost\tINSERT INTO fitness_eval_audit.events (table_name, op) VALUES ('{table}', '{event}')"
        for table in lab._TABLES
        for event in lab._EVENTS
    ]


def test_integrity_rejects_missing_trigger_changed_owner_or_cascade(monkeypatch):
    rows = trigger_rows()
    output = ["\n".join(rows), f"{len(lab._TABLES)}\n0\n1"]
    monkeypatch.setattr(lab, "_sql", lambda *_args, **_kw: output.pop(0))
    assert lab._integrity("container") is True
    for first, second in [
        ("\n".join(rows[:-1]), f"{len(lab._TABLES)}\n0\n1"),
        (
            "\n".join([rows[0].replace("root@localhost", "other@localhost"), *rows[1:]]),
            f"{len(lab._TABLES)}\n0\n1",
        ),
        ("\n".join(rows), f"{len(lab._TABLES)}\n1\n1"),
        ("\n".join(rows), f"{len(lab._TABLES)}\n0\n0"),
    ]:
        values = [first, second]
        monkeypatch.setattr(lab, "_sql", lambda *_args, _values=values, **_kw: _values.pop(0))
        assert lab._integrity("container") is False


def test_denial_check_does_not_treat_network_error_as_permission_denial(monkeypatch):
    monkeypatch.setattr(
        lab.subprocess,
        "run",
        lambda *_args, **_kw: SimpleNamespace(returncode=1, stderr="connection failed", stdout=""),
    )
    with pytest.raises(PreflightError, match="EXPECTED_ACCESS_DENIAL_NOT_OBSERVED"):
        lab._sql("container", "some sql", writer=True, deny=True)
    monkeypatch.setattr(
        lab.subprocess,
        "run",
        lambda *_args, **_kw: SimpleNamespace(
            returncode=1, stderr="ERROR 1142 (42000): denied", stdout=""
        ),
    )
    assert lab._sql("container", "some sql", writer=True, deny=True) == ""


def test_preflight_failure_occurs_before_any_setup(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///fake.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(lab.Path, "is_socket", lambda _self: True)

    def reject(**kwargs):
        raise PreflightError("NOT_DISPOSABLE")

    monkeypatch.setattr(lab, "check_mysql", reject)
    monkeypatch.setattr(lab, "_sql", lambda *_args, **_kw: pytest.fail("setup ran"))
    with pytest.raises(PreflightError, match="NOT_DISPOSABLE"):
        lab.run(run_id="x", container_id="y", port=1)


@pytest.mark.parametrize("host", ["", "tcp://localhost:2375", "unix://relative.sock"])
def test_nonlocal_docker_endpoint_is_rejected_before_inspection(monkeypatch, host):
    monkeypatch.setenv("DOCKER_HOST", host)
    monkeypatch.setattr(lab, "check_mysql", lambda **_kwargs: pytest.fail("inspection ran"))
    with pytest.raises(PreflightError, match="LOCAL_DOCKER_SOCKET_REQUIRED"):
        lab.run(run_id="x", container_id="y", port=1)
