"""执行 Agent PostgreSQL 逻辑备份与恢复验收。

默认只检查 PostgreSQL 容器、源数据库和 ``pg_dump``/``pg_restore`` 工具是否可用。
显式传入 ``--execute`` 后，脚本会：

1. 从源数据库生成临时 Custom Format 逻辑备份；
2. 在同一 PostgreSQL 容器中创建唯一临时数据库并恢复备份；
3. 对源库和恢复库的 public 表逐表比较记录数；
4. 在 finally 中删除临时数据库。

脚本不会执行 ``DROP``、``TRUNCATE`` 或覆盖源数据库，也不会修改 MySQL 业务库。该验收
验证的是“备份文件可生成、可恢复、恢复后数据规模一致”，不等同于生产级跨区域备份、WAL
归档或灾备切换演练。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


class PostgresBackupRestoreCheckError(RuntimeError):
    """PostgreSQL 备份恢复验收未达到预期。"""


@dataclass(frozen=True)
class CheckConfig:
    """本地 PostgreSQL 容器验收参数；密码只从环境变量传递给容器命令。"""

    container: str
    database: str
    username: str
    password: str
    timeout_seconds: int
    execute: bool
    rto_target_seconds: float


def build_parser() -> argparse.ArgumentParser:
    """构造参数；默认只读，避免误执行数据库创建和恢复。"""

    parser = argparse.ArgumentParser(description="验收 Agent PostgreSQL 备份与恢复")
    parser.add_argument(
        "--container",
        default=os.getenv("AGENT_POSTGRES_CONTAINER", "fitness-agent-postgres"),
        help="PostgreSQL Docker 容器名，默认 fitness-agent-postgres",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("AGENT_POSTGRES_DATABASE", "fitness_agent"),
        help="源数据库名，默认 fitness_agent",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("AGENT_POSTGRES_USERNAME", "fitness_agent"),
        help="数据库账号，默认 fitness_agent",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("AGENT_POSTGRES_PASSWORD", "fitness_agent"),
        help="数据库密码，默认读取 AGENT_POSTGRES_PASSWORD",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("AGENT_POSTGRES_BACKUP_TIMEOUT_SECONDS", "180")),
        help="单个 Docker 操作超时时间，默认 180 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.getenv("AGENT_LIVE_EXECUTE_WRITES") == "1",
        help="生成备份并恢复到临时数据库；默认只做前置检查",
    )
    parser.add_argument(
        "--rto-target-seconds",
        type=float,
        default=float(os.getenv("AGENT_POSTGRES_RTO_TARGET_SECONDS", "60")),
        help="恢复加校验的本地 RTO 门槛，默认 60 秒；只在 --execute 时生效",
    )
    return parser


def build_config(args: argparse.Namespace) -> CheckConfig:
    """校验容器、数据库标识，禁止把任意 Shell 片段传入 Docker 命令。"""

    values = {
        "container": str(args.container).strip(),
        "database": str(args.database).strip(),
        "username": str(args.username).strip(),
    }
    if any(not value for value in values.values()):
        raise PostgresBackupRestoreCheckError("容器名、数据库名和账号不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", values["container"]):
        raise PostgresBackupRestoreCheckError("容器名包含不允许的字符")
    for label in ("database", "username"):
        if not re.fullmatch(r"[A-Za-z0-9_]+", values[label]):
            raise PostgresBackupRestoreCheckError(f"{label} 包含不允许的字符")
    if args.timeout_seconds < 10 or args.timeout_seconds > 1800:
        raise PostgresBackupRestoreCheckError("timeout-seconds 必须在 10 到 1800 秒之间")
    if args.rto_target_seconds <= 0 or args.rto_target_seconds > args.timeout_seconds:
        raise PostgresBackupRestoreCheckError(
            "rto-target-seconds 必须大于 0 且不超过 timeout-seconds"
        )
    return CheckConfig(
        **values,
        password=str(args.password),
        timeout_seconds=int(args.timeout_seconds),
        execute=bool(args.execute),
        rto_target_seconds=float(args.rto_target_seconds),
    )


def _docker_exec(
    config: CheckConfig,
    *arguments: str,
    input_data: bytes | None = None,
    label: str,
) -> bytes:
    """运行显式 Docker 命令，不经过 Shell，也不把密码写入日志。"""

    command = ["docker", "exec"]
    if input_data is not None:
        command.append("-i")
    if config.password:
        command.extend(["-e", f"PGPASSWORD={config.password}"])
    command.extend([config.container, *arguments])
    try:
        result = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresBackupRestoreCheckError(f"Docker 操作超时或不可用：{label}") from exc
    if result.returncode != 0:
        # 只保留命令错误输出的最后一行，帮助定位恢复失败原因，同时不回显命令参数、
        # 环境变量或完整数据库日志，避免把密码和过多内部信息带到验收终端。
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        suffix = f"：{detail[-1][:300]}" if detail else ""
        raise PostgresBackupRestoreCheckError(f"Docker 操作失败：{label}{suffix}")
    return result.stdout


def _psql(config: CheckConfig, database: str, sql: str, *, label: str) -> bytes:
    """在指定数据库中执行一条 psql 命令；调用方传入的数据库名均已校验。"""

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
        "-d",
        database,
        "-c",
        sql,
        label=label,
    )


def _quote_identifier(value: str) -> str:
    """安全引用从 PostgreSQL 元数据读取的标识符。"""

    return '"' + value.replace('"', '""') + '"'


def _list_public_tables(config: CheckConfig, database: str) -> list[str]:
    """读取 public schema 的用户表，包含 Agent 业务表和 LangGraph Checkpoint 表。"""

    raw = _psql(
        config,
        database,
        """
        SELECT tablename
        FROM pg_catalog.pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """,
        label=f"读取 {database} 表目录",
    )
    return [line.strip() for line in raw.decode().splitlines() if line.strip()]


def _table_counts(config: CheckConfig, database: str) -> dict[str, int]:
    """逐表统计精确记录数，用于恢复后的数据规模核对。"""

    counts: dict[str, int] = {}
    for table_name in _list_public_tables(config, database):
        quoted_table = _quote_identifier(table_name)
        raw = _psql(
            config,
            database,
            f"SELECT COUNT(*) FROM public.{quoted_table}",
            label=f"统计 {database}.{table_name}",
        )
        try:
            counts[table_name] = int(raw.decode().strip())
        except ValueError as exc:
            raise PostgresBackupRestoreCheckError(
                f"表 {database}.{table_name} 的 COUNT 结果异常"
            ) from exc
    return counts


def _preflight(config: CheckConfig) -> None:
    """确认容器可执行 PostgreSQL 客户端和源库只读可连接。"""

    _docker_exec(config, "pg_isready", "-U", config.username, label="PostgreSQL 存活检查")
    _psql(config, config.database, "SELECT 1", label="源数据库只读连接检查")
    _docker_exec(config, "pg_dump", "--version", label="pg_dump 工具检查")
    _docker_exec(config, "pg_restore", "--version", label="pg_restore 工具检查")


def _dump_database(config: CheckConfig) -> bytes:
    """生成 Custom Format 备份；不使用 --clean，保证恢复不会删除目标库之外的数据。"""

    return _docker_exec(
        config,
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        config.database,
        "--username",
        config.username,
        label="生成 PostgreSQL 逻辑备份",
    )


def _create_database(config: CheckConfig, database: str) -> None:
    """创建只属于本轮验收的临时数据库。"""

    _docker_exec(
        config,
        "createdb",
        "--template=template0",
        "--owner",
        config.username,
        "--username",
        config.username,
        database,
        label="创建临时恢复数据库",
    )


def _restore_database(config: CheckConfig, database: str, backup: bytes) -> None:
    """恢复到临时数据库；使用系统临时文件规避 Docker 二进制标准输入流损坏。"""

    container_backup_path = f"/tmp/fitness_agent_restore_{uuid.uuid4().hex}.dump"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="fitness-agent-restore-", suffix=".dump", delete=False
        ) as backup_file:
            temporary_path = backup_file.name
            backup_file.write(backup)
        _docker_copy_to_container(config, temporary_path, container_backup_path)
        _docker_exec(
            config,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            database,
            "--username",
            config.username,
            container_backup_path,
            label="恢复 PostgreSQL 临时数据库",
        )
    finally:
        try:
            _docker_exec(
                config,
                "rm",
                "-f",
                container_backup_path,
                label="删除容器内临时备份",
            )
        except PostgresBackupRestoreCheckError:
            print(f"警告：容器内临时备份 {container_backup_path} 未能自动删除")
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                print(f"警告：宿主机临时备份 {temporary_path} 未能自动删除")


def _docker_copy_to_container(config: CheckConfig, source_path: str, target_path: str) -> None:
    """把本轮临时备份复制到指定容器临时路径，不写入项目或持久化卷。"""

    try:
        result = subprocess.run(
            ["docker", "cp", source_path, f"{config.container}:{target_path}"],
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresBackupRestoreCheckError("Docker 操作超时或不可用：复制临时备份") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        suffix = f"：{detail[-1][:300]}" if detail else ""
        raise PostgresBackupRestoreCheckError(f"Docker 操作失败：复制临时备份{suffix}")


def _drop_database(config: CheckConfig, database: str) -> None:
    """只删除本脚本创建的临时数据库；源数据库名称不会传入此函数。"""

    try:
        _docker_exec(
            config,
            "dropdb",
            "--if-exists",
            "--force",
            "--username",
            config.username,
            database,
            label="删除临时恢复数据库",
        )
    except PostgresBackupRestoreCheckError:
        # 保留主验收异常；清理失败会在输出中提示，但不尝试触碰源库。
        print(f"警告：临时数据库 {database} 未能自动删除，请手工确认后清理")


def _total_rows(counts: dict[str, int]) -> int:
    """计算所有 public 表的总行数，作为恢复规模的可读基线。"""

    return sum(counts.values())


def run(config: CheckConfig) -> None:
    """运行只读前置检查，或执行完整的临时库备份恢复核对。"""

    _preflight(config)
    if not config.execute:
        print("PostgreSQL 备份恢复前置检查通过（未生成备份，未创建临时数据库）")
        print("如需执行完整验收，请追加 --execute")
        return

    temporary_database = f"fitness_agent_restore_{uuid.uuid4().hex[:16]}"
    backup = b""
    restore_started_at: float | None = None
    try:
        source_counts = _table_counts(config, config.database)
        backup_started_at = datetime.now(UTC)
        backup_started_clock = time.perf_counter()
        backup = _dump_database(config)
        backup_seconds = time.perf_counter() - backup_started_clock
        if not backup:
            raise PostgresBackupRestoreCheckError("pg_dump 生成了空备份")
        _create_database(config, temporary_database)
        restore_started_at = time.perf_counter()
        _restore_database(config, temporary_database, backup)
        restore_seconds = time.perf_counter() - restore_started_at
        verification_started_at = time.perf_counter()
        restored_counts = _table_counts(config, temporary_database)
        verification_seconds = time.perf_counter() - verification_started_at
        if source_counts != restored_counts:
            missing = sorted(set(source_counts) - set(restored_counts))
            extra = sorted(set(restored_counts) - set(source_counts))
            changed = sorted(
                table
                for table in set(source_counts) & set(restored_counts)
                if source_counts[table] != restored_counts[table]
            )
            raise PostgresBackupRestoreCheckError(
                f"恢复后表记录数不一致：missing={missing}, extra={extra}, changed={changed}"
            )
        rto_seconds = restore_seconds + verification_seconds
        if rto_seconds > config.rto_target_seconds:
            raise PostgresBackupRestoreCheckError(
                f"恢复 RTO 超过门槛：实际 {rto_seconds:.2f}s，目标 {config.rto_target_seconds:.2f}s"
            )
        digest = hashlib.sha256(backup).hexdigest()
        print("PostgreSQL 备份恢复真实验收通过")
        print(f"backup_bytes={len(backup)}; backup_sha256={digest}")
        print(
            f"public_tables={len(source_counts)}; total_rows={_total_rows(source_counts)}; "
            "row_counts=一致"
        )
        print(
            f"backup_consistency_at={backup_started_at.isoformat()}; "
            f"backup_seconds={backup_seconds:.2f}; restore_seconds={restore_seconds:.2f}; "
            f"verification_seconds={verification_seconds:.2f}; "
            f"rto_seconds={rto_seconds:.2f}; rto_target_seconds={config.rto_target_seconds:.2f}"
        )
        print(
            "rpo_measurement=logical_backup_consistency_point; "
            "生产 RPO 仍需通过备份频率、WAL/PITR 和恢复演练确定"
        )
    finally:
        if temporary_database:
            _drop_database(config, temporary_database)


def main() -> int:
    """命令行入口；错误信息不包含数据库密码。"""

    try:
        args = build_parser().parse_args()
        run(build_config(args))
    except (PostgresBackupRestoreCheckError, OSError) as exc:
        print(f"PostgreSQL 备份恢复验收失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
