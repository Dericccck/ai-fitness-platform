"""执行经营 Redis 固定窗口限流的并发验收。

本脚本只使用一个带唯一后缀的临时 Redis Key，验证高并发下 Lua 原子计数不会超发；
不调用 DeepSeek、不访问 Java Gateway，也不写入 PostgreSQL/MySQL 业务数据。默认只
执行 Redis 前置检查，必须显式传入 ``--execute`` 才会产生临时计数并在结束时清理。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.infrastructure.cache import Cache


class RateLimitLoadCheckError(RuntimeError):
    """Redis 限流并发验收未达到预期。"""


@dataclass(frozen=True)
class LoadCheckConfig:
    """并发验收参数；不保存任何真实机构或用户标识。"""

    redis_url: str
    request_count: int
    limit: int
    window_seconds: int
    execute: bool


def build_parser() -> argparse.ArgumentParser:
    """构造参数；默认只读，避免把前置检查误当成限流压力测试。"""

    parser = argparse.ArgumentParser(description="验收经营 Redis 并发限流")
    parser.add_argument(
        "--redis-url",
        default=os.getenv("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:6380/15"),
        help="测试 Redis 地址，默认读取 AGENT_TEST_REDIS_URL",
    )
    parser.add_argument("--requests", type=int, default=40, help="并发请求数量，默认 40")
    parser.add_argument("--limit", type=int, default=10, help="固定窗口额度，默认 10")
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=30,
        help="固定窗口秒数，默认 30",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行临时 Redis 并发计数；默认只检查 Redis 可连接",
    )
    return parser


def build_config(args: argparse.Namespace) -> LoadCheckConfig:
    """校验并发测试参数，防止一次命令意外制造过大的本地压力。"""

    redis_url = str(args.redis_url).strip()
    request_count = int(args.requests)
    limit = int(args.limit)
    window_seconds = int(args.window_seconds)
    if not redis_url:
        raise RateLimitLoadCheckError("Redis 地址不能为空")
    if request_count < 1 or request_count > 10_000:
        raise RateLimitLoadCheckError("requests 必须在 1 到 10000 之间")
    if limit < 1 or limit > request_count:
        raise RateLimitLoadCheckError("limit 必须在 1 到 requests 之间")
    if window_seconds < 1 or window_seconds > 3600:
        raise RateLimitLoadCheckError("window-seconds 必须在 1 到 3600 之间")
    return LoadCheckConfig(redis_url, request_count, limit, window_seconds, bool(args.execute))


async def run_check(config: LoadCheckConfig) -> None:
    """执行 Redis 前置检查或临时并发限流验收。"""

    cache = Cache(config.redis_url)
    await cache.ping()
    if not config.execute:
        print("Redis 限流前置检查通过（未创建临时计数）")
        await cache.close()
        return

    key = f"fitness-agent:live-check:rate-limit:{uuid.uuid4().hex}"
    started_at = time.perf_counter()
    try:
        results = await asyncio.gather(
            *(
                cache.consume_fixed_window(
                    key,
                    limit=config.limit,
                    window_seconds=config.window_seconds,
                )
                for _ in range(config.request_count)
            )
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        allowed_count = sum(results)
        ttl = int(await cache.client.ttl(key))
        if allowed_count != config.limit:
            raise RateLimitLoadCheckError(
                f"并发限流超发：expected_allowed={config.limit}, actual_allowed={allowed_count}"
            )
        if ttl <= 0:
            raise RateLimitLoadCheckError(f"限流 Key 缺少有效 TTL：ttl={ttl}")
        print("Redis 并发限流验收通过")
        print(
            f"requests={config.request_count}; allowed={allowed_count}; "
            f"rejected={config.request_count - allowed_count}; ttl={ttl}; "
            f"elapsed_ms={elapsed_ms:.1f}"
        )
    finally:
        await cache.client.delete(key)
        await cache.close()


def main() -> int:
    """命令行入口；错误只输出稳定诊断，不泄露 Redis 密码。"""

    try:
        args = build_parser().parse_args()
        asyncio.run(run_check(build_config(args)))
    except (RateLimitLoadCheckError, OSError, RedisError, RuntimeError) as exc:
        print(f"Redis 并发限流验收失败：{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
