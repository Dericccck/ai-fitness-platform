"""The standalone Java/MySQL diagnostic must reject unsafe launch targets."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/task_eval_mysql_booking_observation.sh"


def test_unsupported_mode_does_not_invoke_docker(tmp_path):
    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "TMPDIR": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bad"], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0 and not marker.exists()


def test_remote_docker_host_is_rejected_before_container_creation(tmp_path):
    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
        "DOCKER_HOST": "tcp://remote.example:2375",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--coach-slot-concurrency"],
        env=env,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0 and not marker.exists()
    assert not list(tmp_path.glob("fitness-eval-booking-build.*"))
