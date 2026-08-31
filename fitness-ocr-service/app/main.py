"""独立 OCR 服务的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, status

from .config import Settings, get_settings
from .engine import DocumentEngine, OcrEngineError, OcrEngineUnavailable, PaddleStructureEngine
from .models import OcrResponse
from .service import OcrInputError, PdfOcrService

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: Settings, engine: DocumentEngine) -> None:
        self.settings = settings
        self.engine = engine
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)


def create_app(
    settings: Settings | None = None,
    engine: DocumentEngine | None = None,
) -> FastAPI:
    """显式创建应用，方便契约测试注入模拟引擎。"""

    resolved_settings = settings or get_settings()
    resolved_engine = engine or PaddleStructureEngine(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ocr = AppState(resolved_settings, resolved_engine)
        yield

    app = FastAPI(
        title="健身 OCR 服务",
        version="0.1.0",
        docs_url="/docs" if resolved_settings.api_docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "UP"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        state: AppState = app.state.ocr
        engine_status = state.engine.status()
        if not engine_status.ready:
            raise HTTPException(status_code=503, detail="OCR 引擎尚未就绪")
        return {"status": "READY", "engine": engine_status.engine_name}

    @app.post("/v1/parse", response_model=OcrResponse)
    async def parse(
        file: Annotated[UploadFile, File(...)],
        pages: Annotated[str | None, Form()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> OcrResponse:
        state: AppState = app.state.ocr
        _verify_authorization(state.settings, authorization)
        if file.content_type not in {"application/pdf", "application/octet-stream", None}:
            raise HTTPException(status_code=415, detail="文件必须以 PDF 格式上传")
        content = await file.read(state.settings.max_source_bytes + 1)
        if len(content) > state.settings.max_source_bytes:
            raise HTTPException(status_code=413, detail="文件超过配置的大小限制")
        try:
            return await _run_inference(state, content, pages)
        except OcrInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="OCR 推理超时") from exc
        except (OcrEngineUnavailable, OcrEngineError) as exc:
            logger.exception("OCR 请求失败")
            raise HTTPException(
                status_code=503, detail="OCR 引擎处理文档失败"
            ) from exc

    app.state.ocr = AppState(resolved_settings, resolved_engine)
    return app


async def _run_inference(state: AppState, content: bytes, pages: str | None) -> OcrResponse:
    """运行 OCR；超时线程完成前不释放并发槽。

    Python 线程无法被安全强制终止。如果 HTTP 超时，请求会返回 504，但后台任务继续执行；
    只有后台任务完成后才释放信号量，从而避免 GPU 推理重叠。
    """

    await state.semaphore.acquire()
    service = PdfOcrService(state.settings, state.engine)
    worker = asyncio.create_task(asyncio.to_thread(service.parse, content, pages=pages))
    try:
        result = await asyncio.wait_for(
            asyncio.shield(worker), timeout=state.settings.inference_timeout_seconds
        )
    except TimeoutError:
        asyncio.create_task(_drain_worker(worker, state.semaphore))
        raise
    except asyncio.CancelledError:
        asyncio.create_task(_drain_worker(worker, state.semaphore))
        raise
    except BaseException:
        state.semaphore.release()
        raise
    else:
        state.semaphore.release()
        return result


async def _drain_worker(worker: asyncio.Task[OcrResponse], semaphore: asyncio.Semaphore) -> None:
    """等待脱离请求的推理任务完成，并准确释放一次并发槽。"""

    try:
        await worker
    except Exception:
        logger.exception("请求取消后后台 OCR 推理失败")
    finally:
        semaphore.release()


def _verify_authorization(settings: Settings, authorization: str | None) -> None:
    if not settings.auth_required:
        return
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="OCR 服务身份验证未配置")
    expected = f"Bearer {settings.api_key}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="凭证无效")


app = create_app()
