"""在隔离 PostgreSQL 临时数据库中执行 Alembic 升级和回滚验收。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class MigrationLiveCheckError(RuntimeError):
    """真实迁移验收未达到预期。"""


@dataclass(frozen=True)
class CheckConfig:
    """临时数据库验收参数；源数据库只用于创建和删除临时库。"""

    container: str
    host: str
    port: int
    admin_database: str
    username: str
    password: str
    timeout_seconds: int
    execute: bool
    lock_check: bool = False
    lock_hold_seconds: float = 3.0


def build_parser() -> argparse.ArgumentParser:
    """构造参数；默认只检查 PostgreSQL 客户端和 Alembic，不创建数据库。"""

    parser = argparse.ArgumentParser(description="验收 Agent PostgreSQL Alembic 升级和回滚")
    parser.add_argument(
        "--container",
        default=os.getenv("AGENT_POSTGRES_CONTAINER", "fitness-agent-postgres"),
        help="PostgreSQL Docker 容器名，默认 fitness-agent-postgres",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("AGENT_POSTGRES_HOST", "127.0.0.1"),
        help="宿主机 PostgreSQL 地址，默认 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("AGENT_POSTGRES_PORT", "5433")),
        help="宿主机 PostgreSQL 端口，默认 5433",
    )
    parser.add_argument(
        "--admin-database",
        default=os.getenv("AGENT_POSTGRES_ADMIN_DATABASE", "postgres"),
        help="创建临时数据库时连接的管理库，默认 postgres",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("AGENT_POSTGRES_USERNAME", "fitness_agent"),
        help="创建临时数据库的账号，默认 fitness_agent",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("AGENT_POSTGRES_PASSWORD", "fitness_agent"),
        help="数据库密码，默认读取 AGENT_POSTGRES_PASSWORD",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("AGENT_POSTGRES_MIGRATION_TIMEOUT_SECONDS", "180")),
        help="单个 Docker/Alembic 操作超时时间，默认 180 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.getenv("AGENT_LIVE_EXECUTE_WRITES") == "1",
        help="创建临时数据库并真实执行 upgrade head/downgrade base",
    )
    parser.add_argument(
        "--lock-check",
        action="store_true",
        help="在 0034->0035 迁移前持有表读锁，验证迁移等待和恢复",
    )
    parser.add_argument(
        "--lock-hold-seconds",
        type=float,
        default=3.0,
        help="锁等待验收持锁秒数，默认 3 秒",
    )
    return parser


def build_config(args: argparse.Namespace) -> CheckConfig:
    """校验标识和边界，禁止把任意 Shell 片段传入 Docker 或 Alembic。"""

    values = {
        "container": str(args.container).strip(),
        "host": str(args.host).strip(),
        "admin_database": str(args.admin_database).strip(),
        "username": str(args.username).strip(),
    }
    if any(not value for value in values.values()):
        raise MigrationLiveCheckError("容器名、主机、管理库和账号不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", values["container"]):
        raise MigrationLiveCheckError("容器名包含不允许的字符")
    for label in ("admin_database", "username"):
        if not re.fullmatch(r"[A-Za-z0-9_]+", values[label]):
            raise MigrationLiveCheckError(f"{label} 包含不允许的字符")
    if args.port < 1 or args.port > 65535:
        raise MigrationLiveCheckError("port 必须在 1 到 65535 之间")
    if args.timeout_seconds < 10 or args.timeout_seconds > 1800:
        raise MigrationLiveCheckError("timeout-seconds 必须在 10 到 1800 秒之间")
    if args.lock_hold_seconds < 1 or args.lock_hold_seconds > 30:
        raise MigrationLiveCheckError("lock-hold-seconds 必须在 1 到 30 秒之间")
    if args.lock_check and not args.execute:
        raise MigrationLiveCheckError("--lock-check 必须与 --execute 一起使用")
    return CheckConfig(
        **values,
        port=int(args.port),
        password=str(args.password),
        timeout_seconds=int(args.timeout_seconds),
        execute=bool(args.execute),
        lock_check=bool(args.lock_check),
        lock_hold_seconds=float(args.lock_hold_seconds),
    )


def build_database_url(config: CheckConfig, database: str) -> str:
    """生成给 Alembic 使用的临时库 URL，并对账号密码做 URL 编码。"""

    encoded_username = quote(config.username, safe="")
    encoded_password = quote(config.password, safe="")
    return (
        f"postgresql+asyncpg://{encoded_username}:{encoded_password}"
        f"@{config.host}:{config.port}/{database}"
    )


def _docker_exec(
    config: CheckConfig,
    *arguments: str,
    database: str | None = None,
    label: str,
) -> str:
    """运行不经过 Shell 的 Docker 命令，错误输出只保留最后一行。"""

    command = ["docker", "exec", "-e", f"PGPASSWORD={config.password}", config.container]
    command.extend(arguments)
    if database is not None:
        command.extend(["-d", database])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationLiveCheckError(f"Docker 操作超时或不可用：{label}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        suffix = f"：{detail[-1][:300]}" if detail else ""
        raise MigrationLiveCheckError(f"Docker 操作失败：{label}{suffix}")
    return result.stdout.decode(errors="replace")


def _psql(config: CheckConfig, database: str, sql: str, *, label: str) -> str:
    """在指定数据库执行单条 SQL；数据库名来自受控参数或脚本生成值。"""

    return _docker_exec(
        config,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-t",
        "-U",
        config.username,
        "-c",
        sql,
        database=database,
        label=label,
    )


def _run_alembic(config: CheckConfig, database: str, *arguments: str, label: str) -> float:
    """在宿主机 Agent 工程中执行 Alembic，并只将临时库 URL 注入子进程。"""

    service_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["AGENT_DATABASE_URL"] = build_database_url(config, database)
    environment["AGENT_CHECKPOINT_DATABASE_URL"] = ""
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            ["uv", "run", "alembic", *arguments],
            cwd=service_dir,
            env=environment,
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationLiveCheckError(f"Alembic 操作超时或不可用：{label}") from exc
    elapsed = time.perf_counter() - started_at
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        suffix = f"：{detail[-1][:300]}" if detail else ""
        raise MigrationLiveCheckError(f"Alembic 操作失败：{label}{suffix}")
    return elapsed


def _create_database(config: CheckConfig, database: str) -> None:
    """创建本轮唯一临时数据库，不覆盖已有数据库。"""

    _docker_exec(
        config,
        "createdb",
        "--maintenance-db",
        config.admin_database,
        "--template=template0",
        "--owner",
        config.username,
        "--username",
        config.username,
        database,
        label="创建迁移临时数据库",
    )


def _drop_database(config: CheckConfig, database: str) -> None:
    """只删除脚本生成的临时数据库，源库名称不会传入此函数。"""

    _docker_exec(
        config,
        "dropdb",
        "--maintenance-db",
        config.admin_database,
        "--if-exists",
        "--force",
        "--username",
        config.username,
        database,
        label="删除迁移临时数据库",
    )


def _start_lock_holder(config: CheckConfig, database: str) -> subprocess.Popen[str]:
    """启动只在临时库运行的锁持有进程，模拟线上长事务读取通知表。"""

    command = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"PGPASSWORD={config.password}",
        config.container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-t",
        "-U",
        config.username,
        "-d",
        database,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise MigrationLiveCheckError("无法启动临时 PostgreSQL 锁持有进程") from exc
    assert process.stdin is not None
    process.stdin.write(
        "BEGIN; LOCK TABLE agent_in_app_notifications IN ACCESS SHARE MODE; "
        f"SELECT pg_sleep({config.lock_hold_seconds}); COMMIT;\n"
    )
    process.stdin.close()
    return process


def _wait_for_lock(config: CheckConfig, database: str, process: subprocess.Popen[str]) -> None:
    """轮询 pg_locks，确保迁移开始前读锁已经实际生效，避免测试竞态。"""

    deadline = time.monotonic() + min(config.timeout_seconds, 30)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = process.stderr.read().strip() if process.stderr is not None else ""
            raise MigrationLiveCheckError(f"锁持有进程提前退出：{detail[-300:]}")
        raw = _psql(
            config,
            database,
            """
            SELECT COUNT(*)
            FROM pg_locks AS lock_info
            JOIN pg_class AS table_info ON table_info.oid = lock_info.relation
            WHERE table_info.relname = 'agent_in_app_notifications'
              AND lock_info.mode = 'AccessShareLock'
              AND lock_info.granted
            """,
            label="确认临时读锁已生效",
        ).strip()
        if raw and int(raw) >= 1:
            return
        time.sleep(0.1)
    raise MigrationLiveCheckError("未能在超时窗口内确认临时读锁已生效")


def _finish_lock_holder(config: CheckConfig, process: subprocess.Popen[str]) -> None:
    """等待锁持有进程提交事务，并在异常时终止它，避免锁泄漏到后续验收。"""

    try:
        process.wait(timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise MigrationLiveCheckError("临时锁持有进程未能按时释放") from exc
    stderr = process.stderr.read().strip() if process.stderr is not None else ""
    if process.returncode != 0:
        raise MigrationLiveCheckError(f"临时锁持有进程失败：{stderr[-300:]}")


def _run_lock_check(config: CheckConfig, database: str) -> None:
    """验证 0035 DDL 会等待正在读取通知表的长事务，并在释放后完成。"""

    baseline_seconds = _run_alembic(
        config,
        database,
        "upgrade",
        "20260824_0034",
        label="升级到锁等待验收基线版本",
    )
    lock_holder = _start_lock_holder(config, database)
    try:
        _wait_for_lock(config, database, lock_holder)
        migration_seconds = _run_alembic(
            config,
            database,
            "upgrade",
            "head",
            label="执行持锁状态下的通知约束迁移",
        )
        _finish_lock_holder(config, lock_holder)
        version = _psql(
            config,
            database,
            "SELECT version_num FROM alembic_version",
            label="读取锁等待迁移后的版本",
        ).strip()
        if version != "20260824_0035":
            raise MigrationLiveCheckError(f"锁等待迁移后的版本异常：{version}")
        if migration_seconds < config.lock_hold_seconds * 0.8:
            raise MigrationLiveCheckError(
                f"迁移没有体现预期锁等待：耗时 {migration_seconds:.2f}s，持锁 {config.lock_hold_seconds:.2f}s"
            )
        _run_alembic(config, database, "downgrade", "base", label="回滚锁等待验收数据库")
        print(
            f"PostgreSQL 锁等待真实验收通过：baseline={baseline_seconds:.2f}s "
            f"migration_wait={migration_seconds:.2f}s lock_hold={config.lock_hold_seconds:.2f}s"
        )
    except Exception:
        if lock_holder.poll() is None:
            lock_holder.terminate()
            try:
                lock_holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                lock_holder.kill()
                lock_holder.wait()
        raise


def _preflight(config: CheckConfig) -> None:
    """检查 PostgreSQL 和 Alembic 工具可用，但不执行任何迁移。"""

    _docker_exec(config, "pg_isready", "-U", config.username, label="PostgreSQL 存活检查")
    _psql(config, config.admin_database, "SELECT 1", label="管理库连接检查")
    _run_alembic(config, "postgres", "--version", label="Alembic 工具检查")


def run(config: CheckConfig) -> int:
    """执行前置检查或带数据临时库升级/回滚，并保证临时数据库最终清理。"""

    _preflight(config)
    if not config.execute:
        print("PostgreSQL/Alembic 迁移前置检查通过（未创建临时数据库，未执行迁移）")
        return 0

    database = f"fitness_agent_migration_{uuid.uuid4().hex[:12]}"
    _create_database(config, database)
    legacy_notification_id = f"migration-check-legacy-{uuid.uuid4().hex}"
    new_notification_id = f"migration-check-new-{uuid.uuid4().hex}"
    try:
        if config.lock_check:
            _run_lock_check(config, database)
            return 0
        baseline_seconds = _run_alembic(
            config,
            database,
            "upgrade",
            "20260824_0034",
            label="升级到带数据验收基线版本",
        )
        _psql(
            config,
            database,
            f"""
            INSERT INTO agent_in_app_notifications (
                id, notification_type, subject_user_id, organization_id,
                aggregate_type, aggregate_id, dedupe_key
            ) VALUES (
                '{legacy_notification_id}', 'MEMORY_CANDIDATE_PENDING',
                'migration-check-subject', 'migration-check-org',
                'MEMORY_CANDIDATE', 'migration-check-aggregate',
                '{legacy_notification_id}'
            )
            """,
            label="写入迁移前已有通知数据",
        )
        upgrade_seconds = _run_alembic(
            config,
            database,
            "upgrade",
            "head",
            label="带已有数据升级到 Alembic head",
        )
        version = _psql(
            config,
            database,
            "SELECT version_num FROM alembic_version",
            label="读取升级后的版本",
        ).strip()
        if not version:
            raise MigrationLiveCheckError("升级完成后 alembic_version 没有版本记录")
        preserved_count = _psql(
            config,
            database,
            f"SELECT COUNT(*) FROM agent_in_app_notifications WHERE id = '{legacy_notification_id}'",
            label="检查旧通知数据是否保留",
        ).strip()
        if preserved_count != "1":
            raise MigrationLiveCheckError(
                f"升级后旧通知数据数量异常，期望 1，实际 {preserved_count}"
            )
        _psql(
            config,
            database,
            f"""
            INSERT INTO agent_in_app_notifications (
                id, notification_type, subject_user_id, organization_id,
                aggregate_type, aggregate_id, dedupe_key
            ) VALUES (
                '{new_notification_id}', 'TRAINING_PLAN_PUBLISHED',
                'migration-check-subject', 'migration-check-org',
                'TRAINING_PLAN', 'migration-check-training',
                '{new_notification_id}'
            )
            """,
            label="验证新通知类型约束",
        )
        _psql(
            config,
            database,
            f"DELETE FROM agent_in_app_notifications WHERE id IN ('{legacy_notification_id}', '{new_notification_id}')",
            label="清理迁移数据夹具",
        )
        downgrade_seconds = _run_alembic(
            config,
            database,
            "downgrade",
            "base",
            label="回滚到 Alembic base",
        )
        remaining_tables = _psql(
            config,
            database,
            """
            SELECT COUNT(*)
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public'
              AND tablename <> 'alembic_version'
            """,
            label="检查回滚后的业务表",
        ).strip()
        if remaining_tables != "0":
            raise MigrationLiveCheckError(f"回滚到 base 后仍有 {remaining_tables} 张 public 业务表")
        print(
            f"PostgreSQL 带数据迁移真实验收通过：head={version} "
            f"baseline={baseline_seconds:.2f}s upgrade={upgrade_seconds:.2f}s "
            f"downgrade={downgrade_seconds:.2f}s preserved_rows=1"
        )
        return 0
    finally:
        try:
            _drop_database(config, database)
        except MigrationLiveCheckError as exc:
            print(f"警告：临时数据库 {database} 未能自动删除，请手工确认后清理：{exc}")


def main() -> int:
    """命令行入口；异常只输出脱敏后的定位信息。"""

    try:
        args = build_parser().parse_args()
        return run(build_config(args))
    except MigrationLiveCheckError as exc:
        print(f"迁移真实验收失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
