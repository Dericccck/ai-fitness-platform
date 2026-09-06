import asyncio
import os
from uuid import uuid4

import pytest

from app.infrastructure.cache import (
    Cache,
    SessionLockLost,
    SessionLockManager,
    SessionLockUnavailable,
)


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


@pytest.mark.asyncio
async def test_session_lock_renews_and_fences_a_stale_owner_with_real_redis() -> None:
    """真实验证续租、失锁检测，以及重新取得锁后的单调 fencing token。"""

    redis_url = os.getenv("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:6380/15")
    cache = Cache(redis_url)
    thread_id = f"redis-lock-live-{uuid4()}"
    lock_key = f"fitness:agent:session-lock:{thread_id}"
    fence_key = f"fitness:agent:session-fence:{thread_id}"
    connected = False
    first_token = 0
    try:
        try:
            await cache.ping()
            connected = True
        except Exception as exc:  # noqa: BLE001 - 本地未启动 Redis 时跳过集成测试
            pytest.skip(f"Redis is unavailable at {redis_url}: {type(exc).__name__}")

        manager = SessionLockManager(cache.client, ttl_seconds=3)
        async with manager.hold(thread_id) as lease:
            first_token = lease.fencing_token
            await asyncio.sleep(3.2)
            assert await cache.client.ttl(lock_key) > 0
            with pytest.raises(SessionLockUnavailable):
                async with manager.hold(thread_id):
                    pass

            # 模拟 Redis 故障造成租约丢失；续租线程必须发现 owner 不再存在并熔断旧请求。
            await cache.client.delete(lock_key)
            await asyncio.sleep(1.2)
            with pytest.raises(SessionLockLost):
                lease.ensure_owned()

        async with manager.hold(thread_id) as replacement:
            assert replacement.fencing_token > first_token
    finally:
        if connected:
            await cache.client.delete(lock_key, fence_key)
        await cache.close()
