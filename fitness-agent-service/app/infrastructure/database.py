from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


class StaleCheckpointWriter(RuntimeError):
    """Checkpoint 写入者的 fencing token 已落后于当前会话代次。"""


class FencedCheckpointSaver:
    """为 LangGraph Saver 增加数据库侧 fencing 校验。

    Redis 负责快速互斥，PostgreSQL 中的单调代次负责最终拒绝过期执行者。写入校验和
    Saver 写操作由同一个 PostgreSQL advisory lock 串行化，因此新代次一旦激活，旧
    请求后续的 checkpoint 和 pending writes 都无法提交。
    """

    def __init__(self, delegate: Any, fence_pool: Any) -> None:
        self._delegate = delegate
        self._fence_pool = fence_pool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def activate_fence(self, thread_id: str, fencing_token: int) -> None:
        if not thread_id or fencing_token < 1:
            raise ValueError("会话 fencing 参数无效")
        async with self._fence_pool.connection() as connection, connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread_id,)
                )
                await cursor.execute(
                    """
                    INSERT INTO agent_session_fences (thread_id, fencing_token, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (thread_id) DO UPDATE SET
                        fencing_token = EXCLUDED.fencing_token,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE agent_session_fences.fencing_token < EXCLUDED.fencing_token
                    """,
                    (thread_id, fencing_token),
                )
                await cursor.execute(
                    "SELECT fencing_token FROM agent_session_fences WHERE thread_id = %s",
                    (thread_id,),
                )
                row = await cursor.fetchone()
                if row is None or int(row["fencing_token"]) != fencing_token:
                    raise StaleCheckpointWriter("会话 fencing token 已过期")

    async def aget_tuple(self, config: Any) -> Any:
        return await self._delegate.aget_tuple(config)

    async def alist(
        self,
        config: Any | None,
        *,
        filter: dict[str, Any] | None = None,
        before: Any | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Any]:
        async for item in self._delegate.alist(
            config, filter=filter, before=before, limit=limit
        ):
            yield item

    async def aput(
        self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any
    ) -> Any:
        async with self._write_guard(config):
            return await self._delegate.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        async with self._write_guard(config):
            await self._delegate.aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        # 生命周期清理不属于会话请求写入，不要求持有 Redis 租约。
        await self._delegate.adelete_thread(thread_id)

    @asynccontextmanager
    async def _write_guard(self, config: Any) -> AsyncIterator[None]:
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        thread_id = str(configurable.get("thread_id") or "")
        token = configurable.get("fencing_token")
        if not thread_id or not isinstance(token, int) or token < 1:
            raise StaleCheckpointWriter("Checkpoint 写入缺少有效 fencing token")
        async with self._fence_pool.connection() as connection, connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread_id,)
                )
                await cursor.execute(
                    "SELECT fencing_token FROM agent_session_fences WHERE thread_id = %s",
                    (thread_id,),
                )
                row = await cursor.fetchone()
                if row is None or int(row["fencing_token"]) != token:
                    raise StaleCheckpointWriter("旧请求已失去 Checkpoint 写入权")
                yield


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
        self._fence_pool = AsyncConnectionPool(
            conninfo=settings.checkpoint_conninfo,
            min_size=0,
            max_size=2,
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
        await self._fence_pool.open()
        delegate = self._saver_type(self._pool)
        await delegate.setup()
        async with self._fence_pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_session_fences (
                    thread_id TEXT PRIMARY KEY,
                    fencing_token BIGINT NOT NULL CHECK (fencing_token > 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        self._saver = FencedCheckpointSaver(delegate, self._fence_pool)

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
        await self._fence_pool.close()

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
