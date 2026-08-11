from redis.asyncio import Redis


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
