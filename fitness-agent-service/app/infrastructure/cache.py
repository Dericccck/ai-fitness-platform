import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis


class SessionLockUnavailable(RuntimeError):
    """同一会话已有请求执行，当前请求不能并发修改会话状态。"""


class SessionLockLost(RuntimeError):
    """会话租约已失效，调用方必须停止后续状态写入。"""


class SessionLockLease:
    def __init__(self, manager: "SessionLockManager", key: str, owner: str) -> None:
        self._manager = manager
        self._key = key
        self._owner = owner
        self._lost = False

    def ensure_owned(self) -> None:
        if self._lost:
            raise SessionLockLost("会话锁租约已失效")

    def mark_lost(self) -> None:
        self._lost = True


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

    _RENEW_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(self, client: Redis, *, ttl_seconds: int = 60) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    @asynccontextmanager
    async def hold(self, thread_id: str) -> AsyncIterator[SessionLockLease]:
        key = f"fitness:agent:session-lock:{thread_id}"
        owner = str(uuid4())
        acquired = await self.client.set(key, owner, nx=True, ex=self.ttl_seconds)
        if not acquired:
            raise SessionLockUnavailable("会话正在处理中")
        lease = SessionLockLease(self, key, owner)
        stop = asyncio.Event()

        async def renew() -> None:
            interval = max(1.0, self.ttl_seconds / 3)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    continue
                except asyncio.TimeoutError:
                    pass
                try:
                    renewed = await cast(
                        Awaitable[Any],
                        self.client.eval(
                            self._RENEW_SCRIPT, 1, key, owner, str(self.ttl_seconds)
                        ),
                    )
                except Exception:
                    lease.mark_lost()
                    return
                if int(renewed or 0) != 1:
                    lease.mark_lost()
                    return

        renew_task = asyncio.create_task(renew())
        try:
            yield lease
        finally:
            stop.set()
            renew_task.cancel()
            await asyncio.gather(renew_task, return_exceptions=True)
            await cast(Awaitable[Any], self.client.eval(self._RELEASE_SCRIPT, 1, key, owner))


class Cache:
    """Redis 基础适配器。

    后续用于短期会话状态、LangGraph Checkpoint 辅助缓存、限流和幂等控制。
    业务长期事实不能只保存在 Redis 中。
    """

    def __init__(self, redis_url: str) -> None:
        self.client: Redis = Redis.from_url(redis_url, decode_responses=True)

    _FIXED_WINDOW_SCRIPT = """
    local current = redis.call('incr', KEYS[1])
    if current == 1 then
        redis.call('expire', KEYS[1], ARGV[1])
    end
    return current
    """

    async def consume_fixed_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> bool:
        """原子消费一个 Redis 固定窗口配额。

        ``INCR`` 和首次 ``EXPIRE`` 在 Lua 脚本内执行，避免并发请求在设置过期时间前
        看到不一致状态。Redis 只保存计数器，不保存业务查询参数或查询结果；长期业务
        事实仍然必须落在 PostgreSQL/MySQL 中。
        """

        if not key or limit < 1 or window_seconds < 1:
            raise ValueError("固定窗口限流参数无效")
        current = await cast(
            Awaitable[Any],
            self.client.eval(self._FIXED_WINDOW_SCRIPT, 1, key, str(window_seconds)),
        )
        return int(current) <= limit

    async def ping(self) -> None:
        """验证 Redis 当前可连接，供 readiness 使用。"""

        await self.client.ping()

    async def close(self) -> None:
        """关闭 Redis 连接池，避免进程退出时泄漏连接。"""

        await self.client.aclose()
