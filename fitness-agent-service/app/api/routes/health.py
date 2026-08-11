from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.infrastructure.model_gateway import redact_provider_config

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
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
    body = {"status": "ready" if ready_state else "not_ready", "checks": checks}
    if not ready_state:
        response.status_code = 503
    return body


@router.get("/config")
async def config(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "environment": request.app.state.settings.environment,
            "providers": redact_provider_config(request.app.state.settings),
        }
    )
