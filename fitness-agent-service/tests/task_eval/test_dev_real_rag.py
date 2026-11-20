"""The development runner must refuse unproven isolation before any DB write."""

import os
import subprocess
from pathlib import Path

import pytest

from app.evaluation.task_eval import dev_real_rag as rag

SERVICE = Path(__file__).resolve().parents[2]
SCRIPT = SERVICE / "scripts/task_eval_rag_real.sh"


def environment() -> dict[str, str]:
    return {
        "TASK_EVAL_RAG_RUN_ID": "a" * 16,
        "TASK_EVAL_RAG_PG_ID": "b" * 64,
        "TASK_EVAL_RAG_NETWORK_ID": "c" * 64,
        "TASK_EVAL_RAG_PG_PORT": "35432",
        "POSTGRES_PASSWORD": "synthetic-password",
        "AGENT_DATABASE_URL": "postgresql+asyncpg://fitness_eval:synthetic-password@127.0.0.1:35432/postgres",
    }


def test_isolation_checks_container_network_ephemeral_volume_and_exact_port(monkeypatch):
    values = iter(
        [
            f"{'a' * 16}|pgvector/pgvector:pg16|{'c' * 64}|true",
            '{"/var/lib/postgresql/data":""}',
            f"false|{'a' * 16}",
            f'{{"{"b" * 64}":{{}}}}',
            "127.0.0.1:35432",
        ]
    )
    monkeypatch.setattr(rag, "_docker_output", lambda *args: next(values))
    rag.validate_isolated_environment(environment())


@pytest.mark.parametrize(
    "name,value",
    [
        (
            "AGENT_DATABASE_URL",
            "postgresql+asyncpg://fitness_eval:p@production.example:35432/postgres",
        ),
        ("TASK_EVAL_RAG_PG_PORT", "5432;bad"),
        ("TASK_EVAL_RAG_PG_ID", "invalid"),
    ],
)
def test_invalid_database_or_container_never_reaches_docker(monkeypatch, name, value):
    env = environment()
    env[name] = value
    monkeypatch.setattr(
        rag, "_docker_output", lambda *args: pytest.fail("unexpected docker inspection")
    )
    with pytest.raises(ValueError):
        rag.validate_isolated_environment(env)


def test_remote_docker_host_is_rejected_before_any_command(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "called"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": "tcp://remote:2375"}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    assert not marker.exists()


def test_remote_docker_context_is_rejected_before_network_create(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "calls"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{marker}'\n"
        "if [ \"$1 $2\" = 'context show' ]; then echo remote; "
        "elif [ \"$1 $2\" = 'context inspect' ]; then echo tcp://remote:2375; "
        "else exit 9; fi\n"
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": ""}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    calls = marker.read_text()
    assert "context show" in calls and "context inspect" in calls
    assert "network create" not in calls and " run " not in calls


def test_cleanup_attempts_network_even_when_container_removal_fails(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "calls"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$1 $2\" >> '{marker}'\n"
        'case "$1 $2" in\n'
        "  'context show') echo local;;\n"
        "  'context inspect') echo unix:///tmp/docker.sock;;\n"
        "  'network create') echo network-id;;\n"
        "  'run -d') echo container-id;;\n"
        "  'exec container-id') exit 0;;\n"
        "  'port container-id') exit 4;;\n"
        "  'rm -f') exit 5;;\n"
        "  'network rm') exit 0;;\n"
        "  *) exit 9;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": ""}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    calls = marker.read_text()
    assert "rm -f" in calls and "network rm" in calls
