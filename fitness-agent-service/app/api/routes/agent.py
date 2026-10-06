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

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
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
from app.proactive.candidate_repository import ProactiveCandidateRepository
from app.streaming.repository import RunConflict, request_digest

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


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
            thread_id=conversation_thread_id(payload.conversation_id, identity),
            locale=payload.locale,
            identity=identity,
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
    return request_digest(
        {
            "subject": identity.subject,
            "organizations": sorted(identity.organization_ids),
            "roles": sorted(identity.roles),
            "capabilities": sorted(identity.capabilities),
            "qualifications": sorted(identity.qualifications),
        }
    )


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
            payload.run_id, owner, request_digest(body.model_dump()), request_id, execute
        )
    except RunConflict as exc:
        raise HTTPException(409, "任务 ID 已被使用，请勿修改原请求后重试") from exc
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
    return {
        "run_id": run.run_id,
        "status": run.status,
        "response": run.result,
        "error_code": run.error_code,
    }
