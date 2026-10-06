"""创建唯一临时 PG 库，验收迁移/用量/加密任务/真实 Redis，然后只删除该临时库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings


async def check(*, execute: bool) -> dict[str, object]:
    settings = Settings()
    url = make_url(settings.database_url)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    database = "fitness_runtime_acceptance_" + uuid4().hex[:12]
    created = False
    environment = os.environ.copy()
    environment.update(
        AGENT_DATABASE_URL=url.set(database=database).render_as_string(hide_password=False),
        AGENT_REDIS_URL=settings.redis_url,
        AGENT_CHECKPOINT_DATABASE_URL="",
        AGENT_RUN_RUNTIME_DELIVERY_TESTS="1",
    )

    async def run(*args: str) -> None:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, *args],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            timeout=240,
            check=False,
        )
        if result.returncode:
            # 不暴露连接串、供应商错误正文或密码。
            raise RuntimeError(f"验收命令失败：{' '.join(args[:3])}，退出码 {result.returncode}")

    try:
        async with admin.connect() as connection:
            await connection.execute(text("SELECT 1"))
            if not execute:
                return {"passed": True, "mode": "preflight", "runtime_tested": False}
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        await run("-m", "alembic", "upgrade", "head")
        await run(
            "-m",
            "pytest",
            "-q",
            "tests/test_runtime_delivery_postgres.py",
            "tests/test_browser_auth_redis.py",
        )
        await run("-m", "alembic", "downgrade", "20260918_0044")
        await run("-m", "alembic", "upgrade", "head")
        await run("-m", "alembic", "downgrade", "base")
        return {
            "passed": True,
            "mode": "isolated_runtime_acceptance",
            "tests": 6,
            "migration": "head -> 0044 -> head -> base",
            "temporary_database_removed": True,
        }
    finally:
        if created:
            async with admin.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db AND pid<>pg_backend_pid()"
                    ),
                    {"db": database},
                )
                await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(check(execute=args.execute)), ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - 只输出类型，避免数据库错误泄漏连接配置
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
