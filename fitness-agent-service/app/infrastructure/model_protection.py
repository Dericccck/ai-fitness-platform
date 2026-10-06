"""模型资源保护；进程内熔断与共享 Redis 并发租约的职责明确分离。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError


class ModelCapacityError(RuntimeError):
    """模型忙、熔断或共享配额不可用；禁止静默绕过保护。"""


class CircuitBreaker:
    """连续故障熔断，半开阶段只放行一次探测；实例之间不共享故障计数。"""

    def __init__(self, threshold: int, cooldown: float) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.open_until = 0.0
        self.probing = False

    def enter(self) -> bool:
        if self.open_until:
            if monotonic() < self.open_until or self.probing:
                raise ModelCapacityError("模型服务暂时熔断")
            self.probing = True
            return True
        return False

    def success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.probing = False

    def failure(self, *, probe: bool) -> None:
        self.failures += 1
        if probe or self.failures >= self.threshold:
            self.open_until = monotonic() + self.cooldown
        self.probing = False

    def abandon(self, *, probe: bool) -> None:
        # 本地取消、鉴权/参数错误不增加供应商故障计数，但必须释放半开许可。
        if probe:
            self.probing = False


class RedisModelLease:
    """共享模型并发槽；Redis 服务端时间避免实例时钟漂移。

    一次逻辑模型调用（含有界重试）独占一个槽，租约超过所有尝试和退避的总截止时间。
    到期槽会被下一次领取清除，进程被 SIGKILL 后不会永久占用。
    """

    _ACQUIRE = """
    local clock = redis.call('TIME')
    local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
    if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
    redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
    redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[2]) + 5))
    return 1
    """

    def __init__(self, client: Redis, *, provider: str, limit: int, ttl: float) -> None:
        digest = hashlib.sha256(provider.encode()).hexdigest()[:24]
        self.client = client
        self.key = f"fitness:agent:model-slots:{digest}"
        self.limit = limit
        self.ttl = ttl

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        owner = uuid4().hex
        try:
            acquired = await cast(
                Awaitable[Any],
                self.client.eval(self._ACQUIRE, 1, self.key, str(self.limit), str(self.ttl), owner),
            )
        except Exception as exc:
            raise ModelCapacityError("共享模型配额不可用") from exc
        if int(acquired) != 1:
            raise ModelCapacityError("模型并发额度已耗尽")
        try:
            yield
        finally:
            # 释放失败依靠有限租约回收，不覆盖已提交的模型/业务结果。
            try:
                await self.client.zrem(self.key, owner)
            except RedisError as exc:
                structlog.get_logger(__name__).warning(
                    "model_slot_release_failed", error_type=type(exc).__name__
                )


class ModelProtection:
    def __init__(self, concurrency: int, queue_timeout: float, breaker: CircuitBreaker) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.queue_timeout = queue_timeout
        self.breaker = breaker
        self.shared: RedisModelLease | None = None

    @asynccontextmanager
    async def capacity(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.queue_timeout)
        except TimeoutError as exc:
            raise ModelCapacityError("模型等待队列已满") from exc
        try:
            if self.shared is None:
                yield
            else:
                async with self.shared.hold():
                    yield
        finally:
            self.semaphore.release()
