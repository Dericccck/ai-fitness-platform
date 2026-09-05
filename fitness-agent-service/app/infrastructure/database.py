from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


class CheckpointStore:
    """LangGraph PostgreSQL Checkpointer 的生命周期适配器。

    Checkpointer 使用独立 psycopg 连接池，因为 LangGraph 的异步 Saver 需要 psycopg
    协议；普通业务查询仍使用下方 SQLAlchemy AsyncEngine。两者共享同一 PostgreSQL
    实例，但职责分离，避免 Agent 状态和健身业务事实混用。
    """

    def __init__(self, settings: Settings) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            conninfo=settings.checkpoint_conninfo,
            min_size=settings.checkpoint_pool_min_size,
            max_size=settings.checkpoint_pool_max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._saver_type: Any = AsyncPostgresSaver
        self._saver: Any = None

    async def start(self) -> None:
        """打开连接池并执行官方 Checkpoint 表迁移。"""

        await self._pool.open()
        self._saver = self._saver_type(self._pool)
        await self._saver.setup()

    @property
    def saver(self) -> Any:
        """返回已经初始化的 Saver，启动顺序错误时立即失败。"""

        if self._saver is None:
            raise RuntimeError("Checkpoint 存储尚未启动")
        return self._saver

    async def ping(self) -> None:
        """执行 Checkpointer 专用连接池的只读探活。"""

        async with self._pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute("SELECT 1")
            await cursor.fetchone()

    async def close(self) -> None:
        """关闭 Checkpointer 连接池。"""

        await self._pool.close()

    async def delete_threads(self, thread_ids: Sequence[str]) -> int:
        """删除指定会话的 Checkpoint 派生状态。

        只接受由授权生命周期仓储发现的 thread_id，且使用参数化查询；不提供按
        通配符或全表删除入口。LangGraph 官方 Postgres Saver 当前使用三张 thread
        关联表，删除顺序遵循外键依赖。
        """

        ids = [thread_id for thread_id in thread_ids if thread_id and thread_id.strip()]
        if not ids:
            return 0
        deleted = 0
        async with self._pool.connection() as connection, connection.transaction():
            async with connection.cursor() as cursor:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await cursor.execute(
                        f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (ids,)
                    )
                    deleted += cursor.rowcount or 0
        return deleted


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
