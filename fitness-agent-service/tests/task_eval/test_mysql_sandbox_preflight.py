from copy import deepcopy
from subprocess import CompletedProcess
from typing import Any

import pytest

from app.evaluation.task_eval import mysql_sandbox_preflight as preflight
from app.evaluation.task_eval.sandbox_preflight import PreflightError

RUN = "a" * 16
MYSQL = "b" * 64


def container() -> dict[str, Any]:
    return {
        "Id": MYSQL,
        "Name": f"/fitness-eval-mysql-{RUN}",
        "State": {"Running": True},
        "Config": {"Image": "mysql:8.0", "Labels": {"fitness.eval": RUN}},
        "HostConfig": {"AutoRemove": True, "Tmpfs": {"/var/lib/mysql": ""}},
        "Mounts": [],
        "NetworkSettings": {
            "Ports": {
                "3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13306"}],
                "33060/tcp": None,
            }
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["State"].update(Running=False),
        lambda c: c["Config"].update(Image="mysql:latest"),
        lambda c: c["Config"]["Labels"].update({"fitness.eval": "another"}),
        lambda c: c["HostConfig"].update(Tmpfs={}),
        lambda c: c["HostConfig"].update(AutoRemove=False),
        lambda c: c["Mounts"].append({"Type": "bind"}),
        lambda c: c["NetworkSettings"]["Ports"]["3306/tcp"][0].update(HostIp="0.0.0.0"),
        lambda c: c["NetworkSettings"]["Ports"].update(
            {"33060/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13360"}]}
        ),
        lambda c: c["NetworkSettings"]["Ports"].update({"80/tcp": []}),
    ],
)
def test_non_disposable_containers_are_rejected(change: Any) -> None:
    info = deepcopy(container())
    change(info)
    with pytest.raises(PreflightError):
        preflight.verify_container(info, run_id=RUN)


def test_wrong_endpoint_never_invokes_mysql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_inspect", lambda _: container())
    called = False

    def execute(*_: Any, **__: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("must not execute")

    monkeypatch.setattr(preflight.subprocess, "run", execute)
    with pytest.raises(PreflightError, match="ENDPOINT_MISMATCH"):
        preflight.check_mysql(run_id=RUN, container_id=MYSQL, port=3306)
    assert not called


@pytest.mark.parametrize(
    "response",
    [
        "fitness_eval\troot@localhost\t1\t0",
        "fitness_eval\troot@localhost\t0\t1",
        "fitness\troot@localhost\t0\t0",
        "fitness_eval\tother@localhost\t0\t0",
    ],
)
def test_nonempty_or_wrong_mysql_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    monkeypatch.setattr(preflight, "_inspect", lambda _: container())
    monkeypatch.setattr(
        preflight.subprocess, "run", lambda *_args, **_kwargs: CompletedProcess([], 0, response, "")
    )
    with pytest.raises(PreflightError, match="MYSQL_NOT_EMPTY_OR_IDENTITY_MISMATCH"):
        preflight.check_mysql(run_id=RUN, container_id=MYSQL, port=13306)


def test_disposable_mysql_check_does_not_authorize_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preflight, "_inspect", lambda _: container())
    calls: list[Any] = []

    def execute(args: Any, **kwargs: Any) -> CompletedProcess[str]:
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, "fitness_eval\troot@localhost\t0\t0\n", "")

    monkeypatch.setattr(preflight.subprocess, "run", execute)
    result = preflight.check_mysql(run_id=RUN, container_id=MYSQL, port=13306)
    assert result["execution_authorized"] is False
    assert result["dependency_status"] == "VERIFIED_DISPOSABLE_LOCAL_MYSQL"
    assert calls[0][0][:3] == ["docker", "exec", MYSQL]
    assert "--protocol=socket" in calls[0][0][5]
    assert "MYSQL_ROOT_PASSWORD" in calls[0][0][5]
    assert "--password" not in str(calls)
