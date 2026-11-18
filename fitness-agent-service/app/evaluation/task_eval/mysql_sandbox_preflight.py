"""Verify a future dedicated MySQL business sandbox; not currently integrated.

This check never accepts application database URLs. It invokes mysql over the
container's own Unix socket and only issues read-only identity/schema queries.
It does not establish Java gateway isolation, audit coverage, outbound network
restrictions, or sufficient conditions for running Agent tasks.

The current isolated MySQL Maven acceptance uses its own container/run naming and
root account. It is a separate environment and does not satisfy this preflight's
required name, label and empty fitness_eval schema. Do not point this command at
that Maven environment or at an existing business database. Setup and invocation:
see evals/task_eval/mysql-preflight.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from .sandbox_preflight import PreflightError, _inspect

_RUN_ID = re.compile(r"[0-9a-f]{16}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_MYSQL_SQL = (
    "SELECT DATABASE(), CURRENT_USER(), "
    "(SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'fitness_eval'), "
    "(SELECT COUNT(*) FROM information_schema.schemata "
    "WHERE schema_name NOT IN ('information_schema','performance_schema','mysql','sys','fitness_eval'))"
)
# Static program and fixed SQL: no untrusted data or secret is interpolated into shell code.
_COMMAND = (
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket -N -B fitness_eval -e "$1"'
)


def verify_container(info: dict[str, Any], *, run_id: str) -> int:
    """Return the exclusive loopback mapping after verifying disposable ownership."""
    if not _RUN_ID.fullmatch(run_id):
        raise PreflightError("INVALID_RUN")
    try:
        config = info["Config"]
        host = info["HostConfig"]
        mounts = info["Mounts"]
        ports = info["NetworkSettings"]["Ports"]
        if (
            not _CONTAINER_ID.fullmatch(info["Id"])
            or info["Name"] != f"/fitness-eval-mysql-{run_id}"
            or info["State"]["Running"] is not True
            or config["Image"] != "mysql:8.0"
            or (config.get("Labels") or {}).get("fitness.eval") != run_id
            or host["AutoRemove"] is not True
            or "/var/lib/mysql" not in (host.get("Tmpfs") or {})
            or not isinstance(mounts, list)
            or any(mount.get("Type") != "tmpfs" for mount in mounts)
            or not isinstance(ports, dict)
            or set(ports) != {"3306/tcp", "33060/tcp"}
            or ports["33060/tcp"] is not None
        ):
            raise PreflightError("CONTAINER_NOT_DISPOSABLE")
        bindings = ports["3306/tcp"]
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise PreflightError("INVALID_PORT_BINDING")
        binding = bindings[0]
        if binding["HostIp"] != "127.0.0.1":
            raise PreflightError("INVALID_PORT_BINDING")
        port = int(binding["HostPort"])
        if not 1 <= port <= 65535:
            raise PreflightError("INVALID_PORT_BINDING")
        return port
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("INVALID_INSPECTION") from exc


def check_mysql(*, run_id: str, container_id: str, port: int) -> dict[str, Any]:
    """Verify container, matching exposed port and an empty dedicated schema."""
    actual_port = verify_container(_inspect(container_id), run_id=run_id)
    if isinstance(port, bool) or not isinstance(port, int) or port != actual_port:
        raise PreflightError("ENDPOINT_MISMATCH")
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "mysql-preflight", _MYSQL_SQL],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("MYSQL_QUERY_FAILED") from exc
    if result.stdout.strip() != "fitness_eval\troot@localhost\t0\t0":
        raise PreflightError("MYSQL_NOT_EMPTY_OR_IDENTITY_MISMATCH")
    return {
        "dependency_status": "VERIFIED_DISPOSABLE_LOCAL_MYSQL",
        "execution_authorized": False,
        "unverified": [
            "gateway_process_and_network_isolation",
            "fixtures_and_cleanup",
            "agent_service_and_identity",
            "independent_write_audit",
            "model_provider_and_budget",
        ],
    }


def main() -> int:
    try:
        raw_port = os.environ.get("TASK_EVAL_MYSQL_PORT", "")
        if not re.fullmatch(r"[0-9]{1,5}", raw_port):
            raise PreflightError("INVALID_PORT_BINDING")
        result = check_mysql(
            run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
            container_id=os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", ""),
            port=int(raw_port),
        )
        print(json.dumps(result))
        return 0
    except Exception as exc:  # noqa: BLE001 - do not emit secrets or command output
        reason = str(exc) if isinstance(exc, PreflightError) else "DEPENDENCY_CHECK_FAILED"
        print(json.dumps({"dependency_status": "FAILED", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
