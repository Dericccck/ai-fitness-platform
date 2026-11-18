"""创建唯一临时 PG 库，验收迁移/用量/加密任务/真实 Redis，然后只删除该临时库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings


def _unskipped_test_count(junit_path: Path) -> int:
    suites = list(ET.parse(junit_path).getroot().iter("testsuite"))
    count = sum(int(suite.get("tests", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
    if count == 0 or skipped:
        raise RuntimeError("隔离验收有跳过的测试或未执行测试")
    return count


async def check(*, execute: bool, isolated: bool = False) -> dict[str, object]:
    settings = Settings(_env_file=None) if isolated else Settings()
    url = make_url(settings.database_url)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    database = "fitness_runtime_acceptance_" + uuid4().hex[:12]
    created = False
    root = Path(__file__).resolve().parents[1]
    # In the disposable launcher, child processes do not inherit model keys,
    # telemetry endpoints, external service settings, or the project's .env.
    temporary_home = (
        tempfile.TemporaryDirectory(prefix="fitness-eval-runtime-") if isolated else None
    )
    workdir = Path(temporary_home.name) if temporary_home else root
    environment = (
        {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        if isolated
        else os.environ.copy()
    )
    alembic_args: list[str] = []
    if isolated:
        environment.update(
            HOME=str(workdir),
            PYTHONPATH=str(root),
            AGENT_ENVIRONMENT="test",
            TASK_EVAL_RESTRICT_NOTIFICATION_NETWORK="1",
        )
        ini = (root / "alembic.ini").read_text(encoding="utf-8")
        ini = ini.replace(
            "script_location = migrations", f"script_location = {root / 'migrations'}"
        )
        ini = ini.replace("prepend_sys_path = .", f"prepend_sys_path = {root}")
        ini_path = workdir / "alembic.ini"
        ini_path.write_text(ini, encoding="utf-8")
        alembic_args = ["-c", str(ini_path)]
    environment.update(
        AGENT_DATABASE_URL=url.set(database=database).render_as_string(hide_password=False),
        AGENT_REDIS_URL=settings.redis_url,
        AGENT_CHECKPOINT_DATABASE_URL="",
        AGENT_RUN_RUNTIME_DELIVERY_TESTS="1",
        AGENT_RUN_POSTGRES_TESTS="1",
    )

    async def run(*args: str) -> None:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, *args],
            cwd=workdir,
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
        await run("-m", "alembic", *alembic_args, "upgrade", "head")
        task_eval_tests = (
            [
                str(root / "tests/task_eval/test_isolated_postgres_chain.py"),
                str(root / "tests/task_eval/test_isolated_postgres_booking_event.py"),
            ]
            if isolated
            else []
        )
        junit_path = workdir / "isolated-acceptance-junit.xml"
        junit_args = [f"--junitxml={junit_path}"] if isolated else []
        await run(
            "-m",
            "pytest",
            "-q",
            str(root / "tests/test_runtime_delivery_postgres.py"),
            str(root / "tests/test_confirmation_postgres.py"),
            str(root / "tests/test_browser_auth_redis.py"),
            str(root / "tests/test_session_summary_postgres.py"),
            str(root / "tests/test_notification_preferences_postgres.py"),
            str(root / "tests/test_memory_candidate_repository_postgres.py"),
            str(root / "tests/test_notification_template_repository_postgres.py"),
            *task_eval_tests,
            *junit_args,
        )
        executed_tests: int | None = None
        if isolated:
            executed_tests = _unskipped_test_count(junit_path)
        await run("-m", "alembic", *alembic_args, "downgrade", "20260918_0044")
        await run("-m", "alembic", *alembic_args, "upgrade", "head")
        await run("-m", "alembic", *alembic_args, "downgrade", "base")
        return {
            "passed": True,
            "mode": "isolated_runtime_acceptance",
            "test_files": [
                "test_runtime_delivery_postgres.py",
                "test_confirmation_postgres.py",
                "test_browser_auth_redis.py",
                "test_session_summary_postgres.py",
                "test_notification_preferences_postgres.py",
                "test_memory_candidate_repository_postgres.py",
                "test_notification_template_repository_postgres.py",
            ]
            + (
                [
                    "task_eval/test_isolated_postgres_chain.py",
                    "task_eval/test_isolated_postgres_booking_event.py",
                ]
                if isolated
                else []
            ),
            "migration": "head -> 0044 -> head -> base",
            "temporary_database_removed": True,
            "executed_tests": executed_tests,
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
        if temporary_home:
            temporary_home.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                asyncio.run(check(execute=args.execute, isolated=args.isolated)), ensure_ascii=False
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - 只输出类型，避免数据库错误泄漏连接配置
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
