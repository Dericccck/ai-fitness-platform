"""Redis availability must never silently disable shared model protection."""

import pytest
from redis.exceptions import ConnectionError

from app.infrastructure.model_protection import (
    CircuitBreaker,
    ModelCapacityError,
    ModelProtection,
    RedisDailyModelBudget,
    RedisModelLease,
)


class RedisWithOutage:
    unavailable = True

    async def eval(self, *args):
        if self.unavailable:
            raise ConnectionError("isolated test outage")
        return 1

    async def zrem(self, *args):
        return 1


async def test_outage_blocks_budget_and_model_capacity_then_recovers():
    redis = RedisWithOutage()
    budget = RedisDailyModelBudget(redis, provider="isolated", limit=10)
    protection = ModelProtection(1, 1, CircuitBreaker(3, 1))
    protection.shared = RedisModelLease(redis, provider="isolated", limit=1, ttl=10)
    with pytest.raises(ModelCapacityError):
        await budget.reserve()
    invoked = False
    with pytest.raises(ModelCapacityError):
        async with protection.capacity():
            invoked = True
    assert not invoked
    redis.unavailable = False
    await budget.reserve()
    async with protection.capacity():
        invoked = True
    assert invoked
