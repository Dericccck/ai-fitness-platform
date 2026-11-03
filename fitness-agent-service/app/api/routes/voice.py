"""语音适配 API：先鉴权再读取录音，朗读内容只来自已完成的服务端 run。"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from structlog.contextvars import get_contextvars

from app.api.routes.agent import _verified_identity
from app.infrastructure.agent_context import AgentIdentity
from app.streaming.control import run_owner
from app.voice.service import VoiceError, VoiceService, validate_recording

router = APIRouter(prefix="/api/v1/agent/voice", tags=["agent-voice"])


async def voice_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, VoiceError)
    headers = {"Cache-Control": "no-store"}
    if exc.status == 429:
        headers["Retry-After"] = "60"
    return JSONResponse(
        {
            "code": exc.code,
            "message": str(exc),
            "request_id": get_contextvars().get("request_id"),
        },
        status_code=exc.status,
        headers=headers,
    )


def service(request: Request) -> VoiceService:
    result = getattr(request.app.state, "voice", None)
    if not request.app.state.settings.voice_enabled or result is None:
        raise VoiceError(503, "VOICE_DISABLED", "语音暂未启用，文字对话仍可使用")
    if getattr(request.app.state, "draining", False):
        raise VoiceError(503, "VOICE_DRAINING", "服务正在更新，请稍后重试")
    return cast(VoiceService, result)


async def quota(
    request: Request, identity: AgentIdentity, operation: Literal["asr", "tts"]
) -> None:
    settings = request.app.state.settings
    if not settings.voice_rate_limit_enabled:
        return
    scopes = [
        (
            f"user:{identity.subject}",
            getattr(settings, f"voice_{operation}_user_requests_per_minute"),
        )
    ]
    scopes += [
        (f"org:{org}", getattr(settings, f"voice_{operation}_organization_requests_per_minute"))
        for org in sorted(identity.organization_ids)
    ]
    try:
        for scope, limit in scopes:
            digest = hashlib.sha256(scope.encode()).hexdigest()
            allowed = await request.app.state.cache.consume_fixed_window(
                f"fitness:agent:voice-quota:{operation}:{digest}",
                limit=limit,
                window_seconds=60,
            )
            if not allowed:
                raise VoiceError(429, "VOICE_QUOTA", "语音请求较多，请稍后重试")
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(503, "VOICE_QUOTA_UNAVAILABLE", "语音配额服务暂时不可用") from exc


@router.get("/config")
async def config(
    request: Request, response: Response, x_agent_context: str | None = Header(default=None)
) -> dict[str, Any]:
    _verified_identity(request, x_agent_context)
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    return {
        "enabled": settings.voice_enabled,
        "max_recording_seconds": settings.voice_max_recording_seconds,
        "max_upload_bytes": settings.voice_max_upload_bytes,
        "max_spoken_chars": settings.voice_max_spoken_chars,
    }


@router.post("/transcriptions")
async def transcribe(
    request: Request, response: Response, x_agent_context: str | None = Header(default=None)
) -> dict[str, Any]:
    identity = _verified_identity(request, x_agent_context)
    voice = service(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "audio/wav":
        raise VoiceError(415, "RECORDING_FORMAT", "请上传 WAV 录音")
    limit = request.app.state.settings.voice_max_upload_bytes
    length = request.headers.get("content-length")
    if length:
        try:
            size = int(length)
        except ValueError as exc:
            raise VoiceError(400, "UPLOAD_LENGTH", "录音请求长度无效") from exc
        if size < 0 or size > limit:
            raise VoiceError(413, "RECORDING_TOO_LARGE", "录音过大，请缩短后重录")
    await quota(request, identity, "asr")
    data = bytearray()
    try:
        async with asyncio.timeout(request.app.state.settings.voice_upload_timeout_seconds):
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise VoiceError(413, "RECORDING_TOO_LARGE", "录音过大，请缩短后重录")
                data.extend(chunk)
    except TimeoutError as exc:
        raise VoiceError(408, "UPLOAD_TIMEOUT", "录音上传超时，请重试") from exc
    audio = bytes(data)
    seconds = validate_recording(audio, request.app.state.settings.voice_max_recording_seconds)
    text = await voice.transcribe(audio)
    response.headers["Cache-Control"] = "no-store"
    return {"text": text, "duration_ms": round(seconds * 1000)}


async def manifest_for_run(
    request: Request, run_id: UUID, identity: AgentIdentity
) -> dict[str, Any]:
    voice = service(request)
    run = await request.app.state.chat_runs.repository.get(str(run_id), run_owner(identity))
    if run is None:
        raise VoiceError(404, "VOICE_RUN_NOT_FOUND", "回复语音已过期或无权读取")
    if run.status != "COMPLETED" or not run.result:
        raise VoiceError(409, "VOICE_RUN_INCOMPLETE", "仅完整回复可朗读，请等待本轮完成")
    answer = run.result.get("answer")
    if not isinstance(answer, str):
        raise VoiceError(422, "SPEECH_EMPTY", "这条回复没有可朗读的文字")
    return voice.manifest(str(run_id), answer)


@router.get("/replies/{run_id}")
async def manifest(
    run_id: UUID,
    request: Request,
    response: Response,
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _verified_identity(request, x_agent_context)
    result = await manifest_for_run(request, run_id, identity)
    response.headers["Cache-Control"] = "no-store"
    return result


class SegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/replies/{run_id}/segments/{index}")
async def segment(
    run_id: UUID,
    index: int,
    payload: SegmentRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> Response:
    identity = _verified_identity(request, x_agent_context)
    result = await manifest_for_run(request, run_id, identity)
    if result["revision"] != payload.revision:
        raise VoiceError(409, "VOICE_REVISION_CHANGED", "朗读内容已更新，请重新点击朗读")
    if not 0 <= index < result["segment_count"]:
        raise VoiceError(404, "VOICE_SEGMENT_NOT_FOUND", "未找到该语音片段")
    await quota(request, identity, "tts")
    data = await service(request).synthesize(
        run_owner(identity), str(run_id), payload.revision, result["segments"][index]["text"]
    )
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})
