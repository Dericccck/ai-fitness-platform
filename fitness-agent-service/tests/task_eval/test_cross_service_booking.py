import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import cross_service_booking as chain
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def _configure(monkeypatch, *, url="http://127.0.0.1:8765", listener="p123\n", parent="456\n"):
    monkeypatch.setenv("TASK_EVAL_RUN_ID", "a" * 16)
    monkeypatch.setenv("TASK_EVAL_MYSQL_CONTAINER_ID", "b" * 64)
    monkeypatch.setenv("TASK_EVAL_POSTGRES_CONTAINER_ID", "c" * 64)
    monkeypatch.setenv("TASK_EVAL_GATEWAY_PID", "123")
    monkeypatch.setenv("TASK_EVAL_LAUNCHER_PID", "456")
    monkeypatch.setattr(chain.os, "getppid", lambda: 456)
    monkeypatch.setenv("TASK_EVAL_CONTEXT_SECRET", "x" * 32)
    monkeypatch.setenv("TASK_EVAL_CONFIRMATION_SECRET", "y" * 32)
    monkeypatch.setattr(chain, "_inspect", lambda value: {"id": value})
    monkeypatch.setattr(chain, "_verify_container", lambda *_args, **_kw: 1234)
    monkeypatch.setattr(chain, "verify_container", lambda *_args, **_kw: 3306)
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://fitness_eval:password@127.0.0.1:1234/fitness_cross_"
        + "a" * 16,
        gateway_base_url=url,
        gateway_http_execution_service_token="z" * 32,
    )
    monkeypatch.setattr(chain, "Settings", lambda **_: settings)
    monkeypatch.setattr(
        chain.subprocess,
        "run",
        lambda args, **_kw: SimpleNamespace(stdout=parent if args[0] == "ps" else listener),
    )


def test_valid_local_environment(monkeypatch):
    _configure(monkeypatch)
    assert chain._environment()[1] == "a" * 16


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8765",
        "http://localhost:8765",
        "https://127.0.0.1:8765",
        "http://127.0.0.1:8765/other",
    ],
)
def test_external_or_mismatched_gateway_is_rejected(monkeypatch, url):
    _configure(monkeypatch, url=url)
    with pytest.raises(PreflightError, match="UNSAFE_SERVICE_ENDPOINT"):
        chain._environment()


def test_gateway_port_must_belong_to_launched_process(monkeypatch):
    _configure(monkeypatch, listener="p456\n")
    with pytest.raises(PreflightError, match="GATEWAY_LISTENER_NOT_OWNED"):
        chain._environment()


def test_real_model_requires_existing_ledger_and_private_key_file(tmp_path):
    key = tmp_path / "model-key"
    key.write_text("synthetic-key-used-for-offline-test")
    key.chmod(0o600)
    ledger = tmp_path / "budget.sqlite"
    params = {
        "credential_file": str(key),
        "budget_file": str(ledger),
        "limit_nano": 10_000_000_000,
        "opening_nano": 46_080_000,
        "minimum_reserved_nano": 46_080_000,
        "confirmed_current_pricing": True,
    }
    with pytest.raises(ValueError, match="existing budget"):
        chain._real_model(**params)
    DurableBudget(ledger, limit_nano=10_000_000_000, opening_nano=46_080_000)
    assert chain._real_model(**params).budget.snapshot()["reserved_nano"] == 46_080_000
    key.chmod(0o644)
    with pytest.raises(ValueError, match="credential"):
        chain._real_model(**params)


def test_real_model_refuses_incomplete_history(tmp_path):
    key = tmp_path / "model-key"
    key.write_text("synthetic-key-used-for-offline-test")
    key.chmod(0o600)
    ledger = tmp_path / "budget.sqlite"
    DurableBudget(ledger, limit_nano=10_000_000_000, opening_nano=46_080_000)
    with pytest.raises(ValueError, match="budget history"):
        chain._real_model(
            credential_file=str(key),
            budget_file=str(ledger),
            limit_nano=10_000_000_000,
            opening_nano=46_080_000,
            minimum_reserved_nano=148_480_000,
            confirmed_current_pricing=True,
        )


def test_gateway_process_must_be_child_of_this_launcher(monkeypatch):
    _configure(monkeypatch, parent="789\n")
    with pytest.raises(PreflightError, match="GATEWAY_LISTENER_NOT_OWNED"):
        chain._environment()


def test_reported_launcher_must_be_the_actual_parent(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("TASK_EVAL_LAUNCHER_PID", "789")
    with pytest.raises(PreflightError, match="UNSAFE_SERVICE_ENDPOINT"):
        chain._environment()


def test_launcher_rejects_remote_docker_before_starting_services(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nprintf 'tcp://remote.example:2376\\n'\n")
    docker.chmod(0o755)
    maven = tmp_path / "mvn"
    marker = tmp_path / "maven-ran"
    maven.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    maven.chmod(0o755)
    script = Path(__file__).parents[2] / "scripts/task_eval_cross_service_booking.sh"
    environment = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TMPDIR=str(tmp_path))
    result = subprocess.run(
        ["bash", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "A local Docker Unix socket is required" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("status,expected_exit", [("PASS", 0), ("INCOMPLETE", 3), ("ERROR", 1)])
def test_task_status_controls_process_exit(monkeypatch, status, expected_exit):
    async def evaluate(**_kwargs):
        return {"status": status, "component_status": "OBSERVED"}

    monkeypatch.setattr(chain, "evaluate", evaluate)
    monkeypatch.setattr("sys.argv", ["cross_service_booking"])
    assert chain.main() == expected_exit
