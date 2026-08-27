"""执行 Agent 及其基础设施重启后的只读恢复检查。

本脚本用于服务或容器重启后验收，不负责主动停止、重启或删除任何服务。它检查 Agent
存活/就绪、Gateway 存活、PostgreSQL、LangGraph Checkpoint 表、Redis、RabbitMQ，以及
主动提醒 Inbox/通知 Outbox 是否存在超过租约时间仍卡在 PROCESSING 的记录。

Redis 会使用一个带 60 秒过期时间的唯一临时键执行 SET/GET/DELETE，这是验证连接和读写
能力所必需的最小测试数据，不保存业务信息，结束时一定尝试删除。其余检查均为只读。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from dataclasses import dataclass

import aio_pika
import asyncpg  # type: ignore[import-untyped]
import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError


class ServiceRecoveryCheckError(RuntimeError):
    """服务重启恢复检查未达到预期。"""


@dataclass(frozen=True)
class RecoveryConfig:
    """恢复验收连接配置；不会把密码写入输出。"""

    agent_url: str
    gateway_url: str
    database_url: str
    redis_url: str
    rabbitmq_url: str
    event_exchange: str
    timeout_seconds: float
    stale_lock_seconds: int
    strict_stale: bool


def build_parser() -> argparse.ArgumentParser:
    """构造重启后恢复检查参数。"""

    parser = argparse.ArgumentParser(description="检查 Agent 服务重启后的依赖与状态恢复")
    parser.add_argument(
        "--agent-url",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8081"),
        help="Gateway 地址，默认读取 AGENT_GATEWAY_BASE_URL",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "AGENT_DATABASE_URL",
            "postgresql+asyncpg://fitness_agent:fitness_agent@127.0.0.1:5433/fitness_agent",
        ),
        help="Agent PostgreSQL 地址，默认读取 AGENT_DATABASE_URL",
    )
    parser.add_argument(
        "--redis-url",
        default=os.getenv("AGENT_REDIS_URL", "redis://127.0.0.1:6380/0"),
        help="Redis 地址，默认读取 AGENT_REDIS_URL",
    )
    parser.add_argument(
        "--rabbitmq-url",
        default=os.getenv(
            "AGENT_PROACTIVE_RABBITMQ_URL",
            "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/",
        ),
        help="RabbitMQ 地址，默认读取 AGENT_PROACTIVE_RABBITMQ_URL",
    )
    parser.add_argument(
        "--event-exchange",
        default=os.getenv("AGENT_PROACTIVE_RABBITMQ_EXCHANGE", "fitness.domain.events"),
        help="主动提醒 Exchange 名称",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_RECOVERY_CHECK_TIMEOUT_SECONDS", "10")),
        help="单项检查超时时间，默认 10 秒",
    )
    parser.add_argument(
        "--stale-lock-seconds",
        type=int,
        default=int(os.getenv("AGENT_RECOVERY_STALE_LOCK_SECONDS", "300")),
        help="超过该秒数仍为 PROCESSING 的记录视为疑似卡死，默认 300 秒",
    )
    parser.add_argument(
        "--strict-stale",
        action="store_true",
        help="发现疑似卡死的 Inbox/Outbox 时返回失败；默认只告警",
    )
    return parser


def build_config(args: argparse.Namespace) -> RecoveryConfig:
    """规范化参数并拒绝无效超时。"""

    if args.timeout_seconds <= 0:
        raise ServiceRecoveryCheckError("timeout-seconds 必须大于 0")
    if args.stale_lock_seconds < 1:
        raise ServiceRecoveryCheckError("stale-lock-seconds 必须大于 0")
    return RecoveryConfig(
        agent_url=str(args.agent_url).strip().rstrip("/"),
        gateway_url=str(args.gateway_url).strip().rstrip("/"),
        database_url=str(args.database_url)
        .strip()
        .replace("postgresql+asyncpg://", "postgresql://", 1),
        redis_url=str(args.redis_url).strip(),
        rabbitmq_url=str(args.rabbitmq_url).strip(),
        event_exchange=str(args.event_exchange).strip(),
        timeout_seconds=float(args.timeout_seconds),
        stale_lock_seconds=int(args.stale_lock_seconds),
        strict_stale=bool(args.strict_stale),
    )


async def _check_http(client: httpx.AsyncClient, url: str, path: str, label: str) -> None:
    """检查 HTTP 服务，不信任系统代理，避免本地服务被代理 502 干扰。"""

    try:
        response = await client.get(url + path)
    except httpx.HTTPError as exc:
        raise ServiceRecoveryCheckError(f"{label} 无法连接") from exc
    if response.status_code >= 400:
        raise ServiceRecoveryCheckError(f"{label} 返回 HTTP {response.status_code}")


async def _check_database(config: RecoveryConfig) -> tuple[int, int, int]:
    """验证数据库和 Checkpoint 表，并统计疑似卡死的 Inbox/Outbox。"""

    connection = await asyncpg.connect(config.database_url, timeout=config.timeout_seconds)
    try:
        await connection.fetchval("SELECT 1")
        for table_name in (
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "agent_proactive_event_inbox",
            "agent_notification_outbox",
        ):
            exists = await connection.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
            if exists is None:
                raise ServiceRecoveryCheckError(f"缺少 PostgreSQL 表：{table_name}")
        inbox_stale = int(
            await connection.fetchval(
                """
                SELECT COUNT(*) FROM agent_proactive_event_inbox
                WHERE status = 'PROCESSING'
                  AND locked_at <= CURRENT_TIMESTAMP - ($1 * INTERVAL '1 second')
                """,
                config.stale_lock_seconds,
            )
            or 0
        )
        outbox_stale = int(
            await connection.fetchval(
                """
                SELECT COUNT(*) FROM agent_notification_outbox
                WHERE status = 'PROCESSING'
                  AND locked_at <= CURRENT_TIMESTAMP - ($1 * INTERVAL '1 second')
                """,
                config.stale_lock_seconds,
            )
            or 0
        )
        checkpoint_count = int(await connection.fetchval("SELECT COUNT(*) FROM checkpoints") or 0)
        return checkpoint_count, inbox_stale, outbox_stale
    finally:
        await connection.close()


async def _check_redis(config: RecoveryConfig) -> None:
    """执行带过期时间的临时 SET/GET/DELETE，确认 Redis 重启后读写链路正常。"""

    client: Redis = Redis.from_url(config.redis_url, decode_responses=True)
    key = f"fitness:recovery-check:{uuid.uuid4().hex}"
    try:
        await client.set(key, "ok", ex=60)
        if await client.get(key) != "ok":
            raise ServiceRecoveryCheckError("Redis 临时键读回结果不一致")
    except RedisError as exc:
        raise ServiceRecoveryCheckError("Redis 读写检查失败") from exc
    finally:
        try:
            await client.delete(key)
        except RedisError:
            # 主检查失败时不覆盖原错误；key 有 60 秒 TTL，不会成为长期业务残留。
            pass
        await client.aclose()


async def _check_rabbitmq(config: RecoveryConfig) -> None:
    """验证 RabbitMQ 连接和主动提醒 Exchange，使用 passive 不创建拓扑。"""

    connection = await aio_pika.connect_robust(config.rabbitmq_url)
    try:
        channel = await connection.channel()
        await channel.declare_exchange(config.event_exchange, passive=True)
    finally:
        await connection.close()


async def run(config: RecoveryConfig) -> None:
    """并行执行恢复检查，最后统一输出状态摘要。"""

    async with httpx.AsyncClient(
        timeout=config.timeout_seconds,
        trust_env=False,
    ) as client:
        await _check_http(client, config.agent_url, "/health/live", "Agent 存活检查")
        await _check_http(client, config.agent_url, "/health/ready", "Agent 就绪检查")
        await _check_http(client, config.gateway_url, "/health/live", "Gateway 存活检查")

    checkpoint_count, inbox_stale, outbox_stale = await _check_database(config)
    await _check_redis(config)
    await _check_rabbitmq(config)
    print("服务重启恢复检查通过")
    print(f"checkpoint_rows={checkpoint_count}; redis=ok; rabbitmq=ok")
    print(f"stale_inbox={inbox_stale}; stale_notification_outbox={outbox_stale}")
    if inbox_stale or outbox_stale:
        message = "发现超过租约时间仍处于 PROCESSING 的记录，请检查 Worker 日志和重试状态"
        if config.strict_stale:
            raise ServiceRecoveryCheckError(message)
        print(f"警告：{message}")


def main() -> int:
    """命令行入口；不输出连接串或业务数据。"""

    try:
        args = build_parser().parse_args()
        asyncio.run(run(build_config(args)))
    except (
        ServiceRecoveryCheckError,
        asyncpg.PostgresError,
        aio_pika.exceptions.AMQPError,
        RedisError,
        OSError,
    ) as exc:
        print(f"服务重启恢复检查失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
