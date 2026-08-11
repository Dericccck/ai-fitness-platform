from redis.asyncio import Redis


class Cache:
    def __init__(self, redis_url: str) -> None:
        self.client: Redis = Redis.from_url(redis_url, decode_responses=True)

    async def ping(self) -> None:
        await self.client.ping()

    async def close(self) -> None:
        await self.client.aclose()
