from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.evaluation.task_eval import sandbox_preflight as preflight

RUN = "a" * 16
PG = "b" * 64
REDIS = "c" * 64


def container(kind: str) -> dict[str, Any]:
    pg = kind == "pg"
    port = "5432/tcp" if pg else "6379/tcp"
    return {
        "Id": PG if pg else REDIS,
        "Name": f"/fitness-eval-{kind}-{RUN}",
        "State": {"Running": True},
        "Config": {
            "Image": "pgvector/pgvector:pg16" if pg else "redis:7.4-alpine",
            "Labels": {"fitness.eval": RUN},
        },
        "HostConfig": {
            "AutoRemove": True,
            "Tmpfs": {"/var/lib/postgresql/data" if pg else "/data": ""},
        },
        "Mounts": [],
        "NetworkSettings": {
            "Ports": {port: [{"HostIp": "127.0.0.1", "HostPort": "15432" if pg else "16379"}]}
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["State"].update(Running=False),
        lambda c: c["Config"].update(Image="postgres:latest"),
        lambda c: c["Config"]["Labels"].update({"fitness.eval": "another"}),
        lambda c: c["HostConfig"].update(Tmpfs={}),
        lambda c: c["Mounts"].append({"Type": "bind"}),
        lambda c: c["NetworkSettings"]["Ports"]["5432/tcp"][0].update(HostIp="0.0.0.0"),
        lambda c: c["NetworkSettings"]["Ports"].update({"80/tcp": []}),
    ],
)
def test_untrusted_container_is_rejected(change: Any) -> None:
    info = deepcopy(container("pg"))
    change(info)
    with pytest.raises(preflight.PreflightError):
        preflight._verify_container(info, run_id=RUN, kind="pg")


@pytest.mark.asyncio
async def test_endpoint_mismatch_rejected_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight, "_inspect", lambda cid: container("pg" if cid == PG else "redis")
    )
    connect = AsyncMock()
    monkeypatch.setattr(preflight, "create_async_engine", connect)
    with pytest.raises(preflight.PreflightError, match="ENDPOINT_MISMATCH"):
        await preflight.check_dependencies(
            run_id=RUN,
            postgres_id=PG,
            redis_id=REDIS,
            database_url="postgresql+asyncpg://fitness_eval:secret@127.0.0.1:5432/postgres",
            redis_url="redis://127.0.0.1:16379/0",
        )
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_inspection_alone_does_not_authorize_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight, "_inspect", lambda cid: container("pg" if cid == PG else "redis")
    )

    class Connection:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        async def execute(self, *_: Any) -> Any:
            class Result:
                def one(self) -> tuple[str, str]:
                    return ("fitness_eval", "postgres")

            return Result()

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        async def dispose(self) -> None:
            pass

    class Cache:
        async def ping(self) -> bool:
            return True

        async def dbsize(self) -> int:
            return 0

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(preflight, "create_async_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(preflight.Redis, "from_url", lambda *_args, **_kwargs: Cache())
    result = await preflight.check_dependencies(
        run_id=RUN,
        postgres_id=PG,
        redis_id=REDIS,
        database_url="postgresql+asyncpg://fitness_eval:secret@127.0.0.1:15432/postgres",
        redis_url="redis://127.0.0.1:16379/0",
    )
    assert result["dependency_status"] == "VERIFIED_DISPOSABLE_LOCAL_CONTAINERS"
    assert result["execution_authorized"] is False
    assert "secret" not in str(result)
