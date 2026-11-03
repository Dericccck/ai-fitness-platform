"""统一 Agent 对话 API。

该路由只接收用户消息和上游认证服务签发的签名 AgentContext，不接收可替代权限
判断的 user_id、organization_id 或 role。请求进入 Supervisor 后，动态业务事实仍
必须经过 Tool Registry 和 Java Gateway；本阶段先提供非流式稳定协议，SSE 将在会话
Checkpoint 和断线恢复边界明确后接入。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.supervisor import (
    SupervisorRequest,
    SupervisorRuntimeError,
    SupervisorSessionBusy,
    UnsupportedLegacyRequest,
)
from app.api.chat_protection import enforce_chat_quota
from app.api.middleware.request_context import normalize_context_id
from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentIdentity,
    conversation_thread_id,
)
from app.infrastructure.gateway_client import GatewayRequestContext
from app.memory.source import MemorySource, MemorySourceConflict, MemorySourceRepository
from app.proactive.candidate_repository import ProactiveCandidateRepository
from app.streaming.control import current_control, run_owner
from app.streaming.repository import ChatRun, RunConflict, RunSessionBusy, request_digest

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
_logger = structlog.get_logger("agent.memory_source")


class AgentChatRequest(BaseModel):
    """对话请求的稳定输入协议。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    message: str = Field(min_length=1, max_length=4000)
    locale: str = Field(default="zh-CN", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")
    proactive_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)


class AgentChatResponse(BaseModel):
    """对话响应，不暴露模型供应商对象或内部异常详情。"""

    conversation_id: str
    answer: str
    route: str
    tool_steps: int
    input_tokens: int | None
    output_tokens: int | None
    status: str = "COMPLETED"
    confirmation_id: str | None = None
    confirmation_summary: dict[str, object] | None = None
    confirmation_expires_at: str | None = None
    intent: str | None = None
    routes: tuple[str, ...] = ()
    route_source: str | None = None
    route_confidence: float | None = None
    request_usage: dict[str, Any] | None = None


@router.post(
    "/chat",
    response_model=AgentChatResponse,
    response_model_exclude_defaults=True,
)
async def chat(
    payload: AgentChatRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
    x_confirmation_token: str | None = Header(default=None),
) -> AgentChatResponse:
    return await _chat_impl(
        payload, request, x_agent_context, x_request_id, x_trace_id, x_confirmation_token
    )


async def _chat_impl(
    payload: AgentChatRequest,
    request: Request,
    x_agent_context: str | None,
    x_request_id: str | None,
    x_trace_id: str | None,
    x_confirmation_token: str | None,
    *,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    consume_quota: bool = True,
) -> AgentChatResponse:
    """处理一次健身 Agent 对话请求。"""

    if not x_agent_context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="必须提供已签名的 AgentContext",
        )

    try:
        identity = request.app.state.context_verifier.verify(x_agent_context)
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="已签名的 AgentContext 无效",
        ) from exc

    if consume_quota:
        await enforce_chat_quota(request, identity)

    request_id = normalize_context_id(x_request_id) or str(uuid4())
    trace_id = normalize_context_id(x_trace_id) or request_id
    supervisor = request.app.state.supervisor
    memory_sources: MemorySourceRepository | None = getattr(
        request.app.state, "memory_sources", None
    )
    memory_source: MemorySource | None = None
    control = current_control()
    thread_id = conversation_thread_id(payload.conversation_id, identity)
    if memory_sources is not None:
        try:
            memory_source = await memory_sources.capture(
                identity=identity,
                thread_id=thread_id,
                source_request_id=control.run_id if control is not None else request_id,
                message=payload.message,
            )
        except MemorySourceConflict as exc:
            raise HTTPException(409, "来源请求 ID 已绑定其他输入") from exc
        except Exception as exc:  # noqa: BLE001 - 记忆辅助故障不阻断普通业务回答
            _logger.warning("memory_source_capture_failed", error_type=type(exc).__name__)
    user_message = payload.message
    if payload.proactive_candidate_id is not None:
        candidate_repository = cast(
            ProactiveCandidateRepository, request.app.state.proactive_candidates
        )
        async with request.app.state.database.engine.begin() as connection:
            candidate = await candidate_repository.get_for_subject(
                connection,
                candidate_id=payload.proactive_candidate_id,
                subject_user_id=identity.subject,
            )
            if candidate is None or candidate.organization_id not in identity.organization_ids:
                raise HTTPException(status_code=404, detail="未找到主动建议")
            await candidate_repository.record_response(
                connection,
                candidate_id=candidate.id,
                subject_user_id=identity.subject,
                response_type="OPENED_CHAT",
                response_value=None,
                conversation_id=payload.conversation_id,
                notification_id=None,
            )
        # 仅注入服务端候选的低敏业务引用；接收人、机构权限和精确写参数仍不进入 Prompt。
        user_message = (
            f"[主动建议上下文 candidate_type={candidate.candidate_type}; "
            f"reason={candidate.reason_code}; aggregate_type={candidate.aggregate_type}; "
            f"aggregate_id={candidate.aggregate_id}]\n{payload.message}"
        )
    try:
        supervisor_request = SupervisorRequest(
            user_message=user_message,
            gateway_context=GatewayRequestContext(
                signed_context=x_agent_context,
                request_id=request_id,
                trace_id=trace_id,
                confirmation_token=x_confirmation_token,
            ),
            conversation_id=payload.conversation_id,
            thread_id=thread_id,
            locale=payload.locale,
            identity=identity,
            # 自动提取只允许走受开关、身份和 epoch 保护的异步链路。
            # 全局关闭时不能回退到旧同步提取，否则会绕过用户的提取开关。
            defer_memory_extraction=True,
        )
        response = (
            await supervisor.stream(supervisor_request, emit)
            if emit is not None
            else await supervisor.invoke(supervisor_request)
        )
    except UnsupportedLegacyRequest as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SupervisorSessionBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="会话正在处理中",
        ) from exc
    except SupervisorRuntimeError as exc:
        # 不把模型、Gateway、Prompt 或签名上下文详情返回给调用方；具体原因通过
        # request_id/trace_id 在结构化日志和 Trace 系统中定位。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent 服务暂时不可用",
        ) from exc

    if memory_sources is not None and memory_source is not None and response.status == "COMPLETED":
        if control is not None and control.completion is not None:
            # 与流式运行终态、会话完成事实和摘要在同一数据库事务提交。
            control.completion.memory_source = memory_source
        elif control is None:
            try:
                await memory_sources.complete(memory_source)
            except Exception as exc:  # noqa: BLE001 - 保留普通对话结果
                _logger.warning("memory_extraction_enqueue_failed", error_type=type(exc).__name__)

    return AgentChatResponse(
        conversation_id=payload.conversation_id,
        answer=response.answer,
        route=response.route,
        tool_steps=response.tool_steps,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        status=response.status,
        confirmation_id=response.confirmation_id,
        confirmation_summary=response.confirmation_summary,
        confirmation_expires_at=response.confirmation_expires_at,
        intent=response.intent,
        routes=response.routes,
        route_source=response.route_source,
        route_confidence=response.route_confidence,
        request_usage=response.request_usage,
    )


class AgentStreamChatRequest(AgentChatRequest):
    run_id: str = Field(
        pattern=r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$"
    )


def _verified_identity(request: Request, signed: str | None) -> AgentIdentity:
    if not signed:
        raise HTTPException(401, "必须提供已签名的 AgentContext")
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(signed))
    except AgentContextVerificationError as exc:
        raise HTTPException(401, "已签名的 AgentContext 无效") from exc


def _run_owner(identity: AgentIdentity) -> str:
    # 有效权限变化后不能继续读取旧范围的结果；不绑定临时签名/过期时间。
    return run_owner(identity)


@router.post("/chat/stream")
async def chat_stream(
    payload: AgentStreamChatRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> StreamingResponse:
    identity = _verified_identity(request, x_agent_context)
    if not request.app.state.settings.chat_stream_enabled:
        raise HTTPException(404, "流式接口未启用")
    owner = _run_owner(identity)
    manager = request.app.state.chat_runs
    existing = await manager.repository.get(payload.run_id, owner)
    if existing is None:
        await enforce_chat_quota(request, identity)
    request_id = normalize_context_id(x_request_id) or str(uuid4())
    body = AgentChatRequest.model_validate(payload.model_dump(exclude={"run_id"}))

    async def execute(emit: Callable[[str, dict[str, Any]], Awaitable[None]]) -> dict[str, Any]:
        response = await _chat_impl(
            body,
            request,
            x_agent_context,
            request_id,
            x_trace_id,
            None,
            emit=emit,
            consume_quota=False,
        )
        return response.model_dump(mode="json", exclude_defaults=True)

    try:
        await manager.start(
            payload.run_id,
            owner,
            request_digest(body.model_dump()),
            request_id,
            execute,
            scope=request_digest(
                {
                    "owner": owner,
                    "thread": conversation_thread_id(payload.conversation_id, identity),
                }
            ),
        )
    except RunConflict as exc:
        raise HTTPException(409, "任务 ID 已被使用，请勿修改原请求后重试") from exc
    except RunSessionBusy as exc:
        raise HTTPException(409, "上一任务正在执行或停止，请稍后继续") from exc
    except RuntimeError as exc:
        raise HTTPException(503, "任务服务暂时不可用") from exc
    return StreamingResponse(
        manager.events(payload.run_id, owner),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "X-Run-ID": payload.run_id,
        },
    )


@router.get("/runs/{run_id}")
async def get_chat_run(
    run_id: str, request: Request, x_agent_context: str | None = Header(default=None)
) -> dict[str, Any]:
    identity = _verified_identity(request, x_agent_context)
    run = await request.app.state.chat_runs.repository.get(run_id, _run_owner(identity))
    if run is None:
        raise HTTPException(404, "未找到任务或结果已过期")
    return _run_response(run)


def _run_response(run: ChatRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "response": run.result,
        "error_code": run.error_code,
        "stop_reason": run.stop_reason,
        "cancel_requested_at": run.cancel_requested_at.isoformat()
        if run.cancel_requested_at
        else None,
        "cancelled_at": run.cancelled_at.isoformat() if run.cancelled_at else None,
    }


class CancelChatRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/runs/{run_id}/cancel")
async def cancel_chat_run(
    run_id: str,
    payload: CancelChatRunRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> JSONResponse:
    identity = _verified_identity(request, x_agent_context)
    try:
        run = await request.app.state.chat_runs.cancel(
            run_id, _run_owner(identity), payload.cancel_request_id
        )
    except Exception as exc:
        raise HTTPException(503, "停止结果暂时无法确认，请保留任务记录并查询") from exc
    if run is None:
        raise HTTPException(404, "未找到任务或结果已过期")
    return JSONResponse(
        _run_response(run), status_code=(202 if run.status == "CANCEL_REQUESTED" else 200)
    )
