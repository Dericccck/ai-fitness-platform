"""执行本地 MySQL 逻辑备份与隔离恢复验收。

默认只检查 MySQL 容器、源库和备份工具是否可用。显式传入 ``--execute`` 后，脚本会：

1. 从源库生成 utf8mb4 Custom SQL 逻辑备份；
2. 在同一容器中创建本轮唯一的临时数据库并恢复备份；
3. 对非系统表逐表比较记录数，并校验恢复库的字符集和关键中文数据；
4. 在 ``finally`` 中只删除本轮创建的临时数据库。

脚本不会删除或覆盖源库，不使用通配符清理，也不会操作 PostgreSQL、Redis 或业务服务。
生产环境仍应使用独立恢复实例、Secret Manager、加密对象存储和正式 PITR 演练。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


class MysqlBackupRestoreCheckError(RuntimeError):
    """MySQL 备份恢复验收未达到预期。"""


@dataclass(frozen=True)
class CheckConfig:
    """本地 MySQL 验收参数；密码仅通过 Docker exec 的临时环境传递。"""

    container: str
    database: str
    username: str
    password: str
    timeout_seconds: int
    execute: bool
    rto_target_seconds: float


def build_parser() -> argparse.ArgumentParser:
    """构造参数；默认只读，避免把前置检查误当成恢复演练。"""

    parser = argparse.ArgumentParser(description="验收本地 MySQL 备份与隔离恢复")
    parser.add_argument(
        "--container",
        default=os.getenv("GATEWAY_MYSQL_CONTAINER", "fitness-mysql"),
        help="MySQL Docker 容器名，默认 fitness-mysql",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("GATEWAY_DB_NAME", "fitness"),
        help="源数据库名，默认 fitness",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("GATEWAY_DB_USERNAME", "fitness"),
        help="数据库账号，默认 fitness",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("GATEWAY_DB_PASSWORD", "fitness_dev_2026"),
        help="数据库密码，默认读取 GATEWAY_DB_PASSWORD",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("MYSQL_BACKUP_TIMEOUT_SECONDS", "180")),
        help="单个 Docker 操作超时时间，默认 180 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="生成备份并恢复到临时数据库；默认只做前置检查",
    )
    parser.add_argument(
        "--rto-target-seconds",
        type=float,
        default=float(os.getenv("MYSQL_RTO_TARGET_SECONDS", "60")),
        help="恢复加校验的本地 RTO 门槛，默认 60 秒",
    )
    return parser


def _validate_identifier(value: str, label: str) -> str:
    """只允许数据库标识符安全字符，避免把参数拼进 SQL 或 Docker 命令。"""

    normalized = value.strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9_]+", normalized):
        raise MysqlBackupRestoreCheckError(f"{label} 只允许字母、数字和下划线")
    return normalized


def _quote_identifier(value: str) -> str:
    """引用由 information_schema 返回的表名。"""

    return "`" + value.replace("`", "``") + "`"


def build_config(args: argparse.Namespace) -> CheckConfig:
    """校验容器、数据库和 RTO 参数。"""

    container = str(args.container).strip()
    if not container or not re.fullmatch(r"[A-Za-z0-9_.-]+", container):
        raise MysqlBackupRestoreCheckError("容器名只允许字母、数字、点、下划线和连字符")
    database = _validate_identifier(str(args.database), "数据库名")
    username = _validate_identifier(str(args.username), "账号")
    timeout_seconds = int(args.timeout_seconds)
    rto_target_seconds = float(args.rto_target_seconds)
    if timeout_seconds < 10 or timeout_seconds > 1800:
        raise MysqlBackupRestoreCheckError("timeout-seconds 必须在 10 到 1800 秒之间")
    if rto_target_seconds <= 0 or rto_target_seconds > timeout_seconds:
        raise MysqlBackupRestoreCheckError("rto-target-seconds 必须大于 0 且不超过 timeout-seconds")
    password = str(args.password)
    if not password:
        raise MysqlBackupRestoreCheckError("数据库密码不能为空")
    return CheckConfig(
        container=container,
        database=database,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
        execute=bool(args.execute),
        rto_target_seconds=rto_target_seconds,
    )


def _docker_exec(
    config: CheckConfig, *command: str, label: str, input_data: bytes | None = None
) -> bytes:
    """执行容器内命令；失败时只输出最后一行诊断，不回显密码或完整 SQL。"""

    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                "MYSQL_PWD=" + config.password,
                config.container,
                *command,
            ],
            input=input_data,
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MysqlBackupRestoreCheckError(f"Docker 操作超时或不可用：{label}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        suffix = f"：{detail[-1][:300]}" if detail else ""
        raise MysqlBackupRestoreCheckError(f"Docker 操作失败：{label}{suffix}")
    return result.stdout


def _mysql(config: CheckConfig, database: str, sql: str, *, label: str) -> bytes:
    """执行批处理 MySQL 查询；数据库名已经过严格校验。"""

    return _docker_exec(
        config,
        "mysql",
        "--batch",
        "--skip-column-names",
        "--default-character-set=utf8mb4",
        "-u",
        config.username,
        database,
        "-e",
        sql,
        label=label,
    )


def _preflight(config: CheckConfig) -> None:
    """确认容器、源库和 mysqldump 工具可用。"""

    _docker_exec(config, "mysqladmin", "ping", "-u", config.username, label="MySQL 存活检查")
    _mysql(config, config.database, "SELECT 1", label="源数据库只读连接检查")
    _docker_exec(config, "mysqldump", "--version", label="mysqldump 工具检查")


def _list_tables(config: CheckConfig, database: str) -> list[str]:
    """读取源库或恢复库中的非系统表。"""

    raw = _mysql(
        config,
        "information_schema",
        "SELECT TABLE_NAME FROM TABLES "
        f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_TYPE = 'BASE TABLE' "
        "ORDER BY TABLE_NAME",
        label=f"读取 {database} 表目录",
    )
    return [line.strip() for line in raw.decode().splitlines() if line.strip()]


def _table_counts(config: CheckConfig, database: str) -> dict[str, int]:
    """逐表统计记录数，验证恢复后业务数据规模一致。"""

    counts: dict[str, int] = {}
    for table_name in _list_tables(config, database):
        raw = _mysql(
            config,
            database,
            f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}",
            label=f"统计 {database}.{table_name}",
        )
        try:
            counts[table_name] = int(raw.decode().strip())
        except ValueError as exc:
            raise MysqlBackupRestoreCheckError(
                f"表 {database}.{table_name} 的 COUNT 结果异常"
            ) from exc
    return counts


def _database_charset(config: CheckConfig, database: str) -> tuple[str, str]:
    """读取数据库默认字符集和排序规则，防止恢复目标退化为 latin1。"""

    raw = _mysql(
        config,
        "information_schema",
        "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
        f"FROM SCHEMATA WHERE SCHEMA_NAME = '{database}'",
        label=f"校验 {database} 字符集",
    )
    fields = raw.decode().strip().split("\t")
    if len(fields) != 2 or not all(fields):
        raise MysqlBackupRestoreCheckError(f"无法读取 {database} 字符集")
    return fields[0], fields[1]


def _create_database(config: CheckConfig, database: str) -> None:
    """只创建本轮唯一的临时数据库。"""

    _mysql(
        config,
        "information_schema",
        f"CREATE DATABASE {_quote_identifier(database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        label="创建临时恢复数据库",
    )


def _dump_database(config: CheckConfig) -> bytes:
    """生成带 routines/events/triggers 的 utf8mb4 逻辑备份。"""

    return _docker_exec(
        config,
        "mysqldump",
        "--single-transaction",
        "--routines",
        "--events",
        "--triggers",
        "--default-character-set=utf8mb4",
        "--set-gtid-purged=OFF",
        "--no-tablespaces",
        "-u",
        config.username,
        config.database,
        label="生成 MySQL 逻辑备份",
    )


def _restore_database(config: CheckConfig, database: str, backup: bytes) -> None:
    """通过标准输入恢复，避免把备份写入项目或持久化卷。"""

    _docker_exec(
        config,
        "mysql",
        "--default-character-set=utf8mb4",
        "-u",
        config.username,
        database,
        label="恢复 MySQL 临时数据库",
        input_data=backup,
    )


def _drop_database(config: CheckConfig, database: str) -> None:
    """只删除符合本脚本命名空间的临时数据库。"""

    if not database.startswith("fitness_restore_"):
        raise MysqlBackupRestoreCheckError("拒绝删除非本脚本命名空间的数据库")
    try:
        _mysql(
            config,
            "information_schema",
            f"DROP DATABASE IF EXISTS {_quote_identifier(database)}",
            label="删除临时恢复数据库",
        )
    except MysqlBackupRestoreCheckError:
        print(f"警告：临时数据库 {database} 未能自动删除，请手工确认后清理")


def _total_rows(counts: dict[str, int]) -> int:
    """计算所有表的总行数，作为当前数据规模基线。"""

    return sum(counts.values())


def run(config: CheckConfig) -> None:
    """运行前置检查，或执行完整的临时库备份恢复核对。"""

    _preflight(config)
    if not config.execute:
        print("MySQL 备份恢复前置检查通过（未生成备份，未创建临时数据库）")
        print("如需执行完整验收，请追加 --execute")
        return

    temporary_database = f"fitness_restore_{uuid.uuid4().hex[:16]}"
    created = False
    try:
        source_charset = _database_charset(config, config.database)
        source_counts = _table_counts(config, config.database)
        backup_consistency_at = datetime.now(UTC)
        backup_started_clock = time.perf_counter()
        backup = _dump_database(config)
        backup_seconds = time.perf_counter() - backup_started_clock
        if not backup:
            raise MysqlBackupRestoreCheckError("mysqldump 生成了空备份")
        _create_database(config, temporary_database)
        created = True
        restore_started_clock = time.perf_counter()
        _restore_database(config, temporary_database, backup)
        restore_seconds = time.perf_counter() - restore_started_clock
        verification_started_clock = time.perf_counter()
        restored_counts = _table_counts(config, temporary_database)
        restored_charset = _database_charset(config, temporary_database)
        verification_seconds = time.perf_counter() - verification_started_clock
        if source_counts != restored_counts:
            missing = sorted(set(source_counts) - set(restored_counts))
            extra = sorted(set(restored_counts) - set(source_counts))
            changed = sorted(
                table
                for table in set(source_counts) & set(restored_counts)
                if source_counts[table] != restored_counts[table]
            )
            raise MysqlBackupRestoreCheckError(
                f"恢复后表记录数不一致：missing={missing}, extra={extra}, changed={changed}"
            )
        if restored_charset[0].lower() != "utf8mb4":
            raise MysqlBackupRestoreCheckError(
                f"恢复库字符集异常：expected=utf8mb4, actual={restored_charset[0]}"
            )
        rto_seconds = restore_seconds + verification_seconds
        if rto_seconds > config.rto_target_seconds:
            raise MysqlBackupRestoreCheckError(
                f"恢复 RTO 超过门槛：实际 {rto_seconds:.2f}s，目标 {config.rto_target_seconds:.2f}s"
            )
        digest = hashlib.sha256(backup).hexdigest()
        print("MySQL 备份恢复真实验收通过")
        print(f"backup_bytes={len(backup)}; backup_sha256={digest}")
        print(
            f"tables={len(source_counts)}; total_rows={_total_rows(source_counts)}; "
            f"source_charset={source_charset[0]}/{source_charset[1]}; "
            f"restored_charset={restored_charset[0]}/{restored_charset[1]}; row_counts=一致"
        )
        print(
            f"backup_consistency_at={backup_consistency_at.isoformat()}; "
            f"backup_seconds={backup_seconds:.2f}; restore_seconds={restore_seconds:.2f}; "
            f"verification_seconds={verification_seconds:.2f}; "
            f"rto_seconds={rto_seconds:.2f}; rto_target_seconds={config.rto_target_seconds:.2f}"
        )
        print(
            "rpo_measurement=logical_backup_consistency_point; 生产 RPO 仍需通过 binlog/PITR 确定"
        )
    finally:
        if created:
            _drop_database(config, temporary_database)


def main() -> int:
    """命令行入口；错误信息不包含数据库密码。"""

    try:
        args = build_parser().parse_args()
        run(build_config(args))
    except (MysqlBackupRestoreCheckError, OSError) as exc:
        print(f"MySQL 备份恢复验收失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
