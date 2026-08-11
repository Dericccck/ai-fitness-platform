from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.infrastructure.model_gateway import redact_provider_config

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """进程存活探针。

    这里只判断 Web 进程能否响应，不访问外部依赖，避免数据库短暂故障导致容器
    被平台反复重启。依赖可用性由 readiness 单独判断。
    """

    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    """流量就绪探针。

    PostgreSQL 和 Redis 必须真实可连接，LLM、Embedding、Reranker 必须完成显式配置。
    任一条件不满足都返回 HTTP 503，让网关或编排平台停止向该实例分发业务流量。
    模型检查只校验配置，不主动调用计费 API；深度连通性检查由独立监控任务执行。
    """

    settings = request.app.state.settings
    checks: dict[str, object] = {
        "database": "unknown",
        "redis": "unknown",
        "llm": settings.llm_configured,
        "embedding": settings.embedding_configured,
        "reranker": settings.reranker_configured,
    }

    try:
        await request.app.state.database.ping()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint must report a stable response
        # 只返回异常类型，避免连接串、主机或凭证进入公开探针响应。
        checks["database"] = {"status": "failed", "error": type(exc).__name__}

    try:
        await request.app.state.cache.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint must report a stable response
        checks["redis"] = {"status": "failed", "error": type(exc).__name__}

    ready_state = (
        checks["database"] == "ok"
        and checks["redis"] == "ok"
        and checks["llm"] is True
        and checks["embedding"] is True
        and checks["reranker"] is True
    )
    body: dict[str, object] = {
        "status": "ready" if ready_state else "not_ready",
        "checks": checks,
    }
    if not ready_state:
        response.status_code = 503
    return body


@router.get("/config")
async def config(request: Request) -> JSONResponse:
    """输出经过脱敏的运行配置摘要，便于部署排查模型是否完成配置。"""

    return JSONResponse(
        {
            "environment": request.app.state.settings.environment,
            "providers": redact_provider_config(request.app.state.settings),
        }
    )
