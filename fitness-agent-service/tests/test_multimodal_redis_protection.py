"""Production-style cross-process limits against explicitly isolated Redis."""

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.infrastructure.model_protection import (
    ModelCapacityError,
    RedisDailyModelBudget,
    RedisModelLease,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENT_MOBILE_TEST_REDIS_URL"), reason="isolated Redis required"
)


async def test_shared_budget_and_concurrency_fail_closed():
    url = os.environ["AGENT_MOBILE_TEST_REDIS_URL"]
    assert url == "redis://127.0.0.1:57043/15"
    redis = Redis.from_url(url, decode_responses=True)
    provider = "test-" + uuid4().hex
    first = RedisDailyModelBudget(redis, provider=provider, limit=3)
    second = RedisDailyModelBudget(redis, provider=provider, limit=3)
    lease1 = RedisModelLease(redis, provider=provider, limit=1, ttl=10)
    lease2 = RedisModelLease(redis, provider=provider, limit=1, ttl=10)
    try:
        results = await asyncio.gather(
            *(first.reserve() if i % 2 else second.reserve() for i in range(15)),
            return_exceptions=True,
        )
        assert sum(value is None for value in results) == 3
        assert all(value is None or isinstance(value, ModelCapacityError) for value in results)
        async with lease1.hold():
            with pytest.raises(ModelCapacityError):
                async with lease2.hold():
                    pass
        async with lease2.hold():
            pass
    finally:
        await redis.delete(first.key, lease1.key)
        await redis.aclose()
