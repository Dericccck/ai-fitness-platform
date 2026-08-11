from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.infrastructure.cache import Cache
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.cache = Cache(settings.redis_url)
    app.state.models = ModelGateway(settings)
    app.state.reranker = RerankerClient(settings)
    try:
        yield
    finally:
        await app.state.models.close()
        await app.state.cache.close()
        await app.state.database.close()


app = FastAPI(
    title="AI Fitness Agent Service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.include_router(health_router)
