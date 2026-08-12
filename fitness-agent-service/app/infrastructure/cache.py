from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis


class SessionLockUnavailable(RuntimeError):
    """同一会话已有请求执行，当前请求不能并发修改会话状态。"""


class SessionLockManager:
    """基于 Redis SET NX 的会话级互斥锁。

    LangGraph Checkpoint 是持久化事实，但同一 thread 的两个请求同时读写仍可能造成
    消息顺序和 checkpoint 父子关系混乱。因此这里使用短租约锁；释放时通过 Lua 原子
    比较 owner，避免旧请求超时后误删新请求刚取得的锁。
    """

    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, client: Redis, *, ttl_seconds: int = 60) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    @asynccontextmanager
    async def hold(self, thread_id: str) -> AsyncIterator[None]:
        key = f"fitness:agent:session-lock:{thread_id}"
        owner = str(uuid4())
        acquired = await self.client.set(key, owner, nx=True, ex=self.ttl_seconds)
        if not acquired:
            raise SessionLockUnavailable("conversation is already being processed")
        try:
            yield
        finally:
            await cast(Awaitable[Any], self.client.eval(self._RELEASE_SCRIPT, 1, key, owner))


class Cache:
    """Redis 基础适配器。

    后续用于短期会话状态、LangGraph Checkpoint 辅助缓存、限流和幂等控制。
    业务长期事实不能只保存在 Redis 中。
    """

    def __init__(self, redis_url: str) -> None:
        self.client: Redis = Redis.from_url(redis_url, decode_responses=True)

    async def ping(self) -> None:
        """验证 Redis 当前可连接，供 readiness 使用。"""

        await self.client.ping()

    async def close(self) -> None:
        """关闭 Redis 连接池，避免进程退出时泄漏连接。"""

        await self.client.aclose()
