from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


class Database:
    """Agent PostgreSQL 连接池。

    该数据库只保存会话、Checkpoint、Memory、RAG 和评测数据。健身合同、预约、
    课时和训练记录等业务事实仍由 Java/MySQL 管理，Agent 禁止通过此连接池直接修改
    业务事实。
    """

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )

    async def ping(self) -> None:
        """执行最小只读查询，供 readiness 判断数据库是否真正可用。"""

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        """释放连接池，主要用于应用优雅停机和集成测试清理。"""

        await self.engine.dispose()
