import os
from uuid import uuid4

import pytest

from app.infrastructure.cache import Cache


@pytest.mark.asyncio
async def test_fixed_window_rate_limit_works_with_real_redis() -> None:
    """使用本地 Redis 验证 Lua 原子计数、过期时间和超额拒绝。"""

    redis_url = os.getenv("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:6380/15")
    cache = Cache(redis_url)
    key = f"fitness-agent:test:operations-rate:{uuid4()}"
    connected = False
    try:
        try:
            await cache.ping()
            connected = True
        except Exception as exc:  # noqa: BLE001 - 本地未启动 Redis 时跳过集成测试
            pytest.skip(f"Redis is unavailable at {redis_url}: {type(exc).__name__}")

        assert await cache.consume_fixed_window(key, limit=2, window_seconds=30) is True
        assert await cache.consume_fixed_window(key, limit=2, window_seconds=30) is True
        assert await cache.consume_fixed_window(key, limit=2, window_seconds=30) is False
        assert await cache.client.ttl(key) > 0
    finally:
        if connected:
            await cache.client.delete(key)
        await cache.close()
