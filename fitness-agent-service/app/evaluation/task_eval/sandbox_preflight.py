"""Verify disposable local database dependencies; never start an agent or model.

Container inspection is a necessary check, not proof of all isolation.  This
command intentionally never authorizes model_sandbox or integration_sandbox.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{16}\Z")


class PreflightError(ValueError):
    """A deliberately non-sensitive preflight failure."""


def _inspect(container_id: str) -> dict[str, Any]:
    if not _CONTAINER_ID.fullmatch(container_id):
        raise PreflightError("INVALID_CONTAINER_ID")
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        rows = json.loads(result.stdout)
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
            or rows[0].get("Id") != container_id
        ):
            raise PreflightError("INVALID_INSPECTION")
        return rows[0]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("CONTAINER_INSPECTION_FAILED") from exc


def _verify_container(info: dict[str, Any], *, run_id: str, kind: str) -> int:
    if not _RUN_ID.fullmatch(run_id) or kind not in {"pg", "redis"}:
        raise PreflightError("INVALID_RUN")
    image, port, data_path = (
        ("pgvector/pgvector:pg16", "5432/tcp", "/var/lib/postgresql/data")
        if kind == "pg"
        else ("redis:7.4-alpine", "6379/tcp", "/data")
    )
    try:
        config = info["Config"]
        host = info["HostConfig"]
        mounts = info["Mounts"]
        ports = info["NetworkSettings"]["Ports"]
        if (
            not _CONTAINER_ID.fullmatch(info["Id"])
            or info["Name"] != f"/fitness-eval-{kind}-{run_id}"
            or info["State"]["Running"] is not True
            or config["Image"] != image
            or (config.get("Labels") or {}).get("fitness.eval") != run_id
            or host["AutoRemove"] is not True
            or data_path not in (host.get("Tmpfs") or {})
            or not isinstance(mounts, list)
            or any(mount.get("Type") != "tmpfs" for mount in mounts)
            or not isinstance(ports, dict)
            or set(ports) != {port}
        ):
            raise PreflightError("CONTAINER_NOT_DISPOSABLE")
        bindings = ports[port]
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise PreflightError("INVALID_PORT_BINDING")
        binding = bindings[0]
        if binding["HostIp"] != "127.0.0.1":
            raise PreflightError("INVALID_PORT_BINDING")
        result = int(binding["HostPort"])
        if not 1 <= result <= 65535:
            raise PreflightError("INVALID_PORT_BINDING")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("INVALID_INSPECTION") from exc


async def check_dependencies(
    *, run_id: str, postgres_id: str, redis_id: str, database_url: str, redis_url: str
) -> dict[str, Any]:
    """Check Docker state and then query only its exact loopback-bound services."""
    pg_port = _verify_container(_inspect(postgres_id), run_id=run_id, kind="pg")
    redis_port = _verify_container(_inspect(redis_id), run_id=run_id, kind="redis")
    pg = make_url(database_url)
    redis = urlsplit(redis_url)
    if (
        pg.drivername != "postgresql+asyncpg"
        or pg.host != "127.0.0.1"
        or pg.port != pg_port
        or pg.username != "fitness_eval"
        or pg.database != "postgres"
        or not pg.password
        or pg.query
        or redis.scheme != "redis"
        or redis.hostname != "127.0.0.1"
        or redis.port != redis_port
        or redis.username is not None
        or redis.password is not None
        or redis.path != "/0"
        or redis.query
        or redis.fragment
    ):
        raise PreflightError("ENDPOINT_MISMATCH")
    engine = create_async_engine(pg, connect_args={"timeout": 3})
    cache = Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                result = await connection.execute(text("SELECT current_user, current_database()"))
                if tuple(result.one()) != ("fitness_eval", "postgres"):
                    raise PreflightError("DATABASE_IDENTITY_MISMATCH")
            if not await cache.ping() or await cache.dbsize() != 0:
                raise PreflightError("REDIS_NOT_EMPTY_OR_UNAVAILABLE")
    finally:
        await cache.aclose()
        await engine.dispose()
    return {
        "dependency_status": "VERIFIED_DISPOSABLE_LOCAL_CONTAINERS",
        "execution_authorized": False,
        "unverified": [
            "agent_service_and_identity",
            "isolated_business_gateway_and_audit",
            "notification_egress",
            "model_provider_and_budget",
            "fixtures_and_cleanup",
        ],
    }


def main() -> int:
    try:
        result = asyncio.run(
            check_dependencies(
                run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
                postgres_id=os.environ.get("TASK_EVAL_POSTGRES_CONTAINER_ID", ""),
                redis_id=os.environ.get("TASK_EVAL_REDIS_CONTAINER_ID", ""),
                database_url=os.environ.get("AGENT_DATABASE_URL", ""),
                redis_url=os.environ.get("AGENT_REDIS_URL", ""),
            )
        )
        print(json.dumps(result))
        return 0
    except Exception as exc:  # noqa: BLE001 - never emit connection details or credentials
        reason = str(exc) if isinstance(exc, PreflightError) else "DEPENDENCY_CHECK_FAILED"
        print(json.dumps({"dependency_status": "FAILED", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
