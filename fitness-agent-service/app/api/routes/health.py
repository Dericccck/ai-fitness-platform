from urllib.parse import urlparse

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


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    """返回发布验收所需的最小版本信息，不暴露密钥和完整运行配置。"""

    settings = request.app.state.settings
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "environment": settings.environment,
    }


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
        "checkpoint": "unknown",
        "redis": "unknown",
        "llm": settings.llm_configured,
        "embedding": settings.embedding_configured,
        "reranker": settings.reranker_configured,
        "fitness_gateway": settings.gateway_configured,
    }

    try:
        await request.app.state.database.ping()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint must report a stable response
        # 只返回异常类型，避免连接串、主机或凭证进入公开探针响应。
        checks["database"] = {"status": "failed", "error": type(exc).__name__}

    try:
        await request.app.state.checkpoint_store.ping()
        checks["checkpoint"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint must report a stable response
        checks["checkpoint"] = {"status": "failed", "error": type(exc).__name__}

    try:
        await request.app.state.cache.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint must report a stable response
        checks["redis"] = {"status": "failed", "error": type(exc).__name__}

    ready_state = (
        checks["database"] == "ok"
        and checks["checkpoint"] == "ok"
        and checks["redis"] == "ok"
        and checks["llm"] is True
        and checks["embedding"] is True
        and checks["reranker"] is True
        and checks["fitness_gateway"] is True
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
    """输出经过脱敏的运行配置摘要，便于部署排查模型和观测链路是否完成配置。"""

    settings = request.app.state.settings
    return JSONResponse(
        {
            "environment": settings.environment,
            "providers": redact_provider_config(settings),
            "observability": _redact_observability_config(settings),
        }
    )


def _redact_observability_config(settings: object) -> dict[str, object]:
    """返回可用于联调的 OTEL/TruLens 状态，绝不返回 endpoint 密码或完整连接串。"""

    otel_configured = bool(getattr(settings, "otel_configured", False))
    database_url = str(getattr(settings, "trulens_database_url", "")).strip()
    return {
        "otel": {
            "configured": otel_configured,
            "trace_sample_ratio": getattr(settings, "otel_trace_sample_ratio", None),
        },
        "trulens": {
            "enabled": bool(getattr(settings, "trulens_enabled", False)),
            "capture_mode": getattr(settings, "trulens_capture_mode", "disabled"),
            "online_export_enabled": bool(
                getattr(settings, "trulens_online_export_enabled", False)
            ),
            "database": _redact_database_target(database_url),
        },
    }


def _redact_database_target(database_url: str) -> dict[str, object]:
    """只保留数据库协议、主机、端口和库名，避免健康接口泄露账号和密码。"""

    if not database_url:
        return {"configured": False}
    try:
        parsed = urlparse(database_url)
        return {
            "configured": True,
            "scheme": parsed.scheme,
            "host": parsed.hostname or "",
            "port": parsed.port,
            "database": parsed.path.lstrip("/"),
        }
    except ValueError:
        return {"configured": True, "scheme": "invalid"}
