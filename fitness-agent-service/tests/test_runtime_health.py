from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.health import router


async def test_missing_runtime_migration_and_drain_fail_readiness() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(
        llm_configured=True,
        embedding_configured=True,
        reranker_configured=True,
        gateway_configured=True,
        chat_stream_enabled=True,
        llm_usage_ledger_enabled=True,
    )
    app.state.database = SimpleNamespace(
        ping=AsyncMock(), runtime_schema_ready=AsyncMock(return_value=False)
    )
    app.state.checkpoint_store = SimpleNamespace(ping=AsyncMock())
    app.state.cache = SimpleNamespace(ping=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health/ready")).status_code == 503
        app.state.database.runtime_schema_ready.return_value = True
        assert (await client.get("/health/ready")).status_code == 200
        app.state.draining = True
        assert (await client.get("/health/ready")).status_code == 503
        assert (await client.get("/health/live")).status_code == 200
