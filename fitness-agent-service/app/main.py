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
    """统一管理进程级基础设施对象的创建与释放。

    数据库连接池、Redis 客户端和模型 SDK 客户端都应在一个服务进程内复用，
    不能在每次请求中重复创建。对象放入 ``app.state`` 后，由 API、Agent 图和工具层
    从同一个容器中获取；退出时按照依赖顺序释放资源，
    避免服务重启或测试结束后残留连接。
    """

    settings = get_settings()

    # app.state 目前承担轻量依赖容器的职责。后续接入 Tracing、Tool Registry 和
    # LangGraph Checkpointer 时仍从这里统一装配，避免业务 Agent 自行读取环境变量。
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.cache = Cache(settings.redis_url)
    app.state.models = ModelGateway(settings)
    app.state.reranker = RerankerClient(settings)
    try:
        yield
    finally:
        # 即使请求处理出现异常，FastAPI 仍会进入 finally，确保连接被关闭。
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
