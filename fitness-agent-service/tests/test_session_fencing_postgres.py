"""需要显式开启的 PostgreSQL Checkpoint fencing 真实契约测试。"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.infrastructure.database import FencedCheckpointSaver, StaleCheckpointWriter

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


class _Delegate:
    def __init__(self) -> None:
        self.writes = 0

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, versions: Any) -> Any:
        self.writes += 1
        return config


async def test_real_postgres_rejects_stale_checkpoint_generation() -> None:
    settings = Settings(_env_file=None)
    pool = AsyncConnectionPool(
        conninfo=settings.checkpoint_conninfo,
        min_size=0,
        max_size=2,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    thread_id = f"fencing-postgres-live-{uuid4()}"
    delegate = _Delegate()
    saver = FencedCheckpointSaver(delegate, pool)
    old_config = {"configurable": {"thread_id": thread_id, "fencing_token": 1}}
    try:
        await pool.open()
        await saver.activate_fence(thread_id, 1)
        await saver.aput(old_config, {}, {}, {})
        await saver.activate_fence(thread_id, 2)

        with pytest.raises(StaleCheckpointWriter, match="失去"):
            await saver.aput(old_config, {}, {}, {})
        assert delegate.writes == 1
    finally:
        if not pool.closed:
            async with pool.connection() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM agent_session_fences WHERE thread_id = %s", (thread_id,)
                )
            await pool.close()
