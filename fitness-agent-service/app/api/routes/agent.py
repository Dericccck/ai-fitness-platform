"""统一 Agent 对话 API。

该路由只接收用户消息和上游认证服务签发的签名 AgentContext，不接收可替代权限
判断的 user_id、organization_id 或 role。请求进入 Supervisor 后，动态业务事实仍
必须经过 Tool Registry 和 Java Gateway；本阶段先提供非流式稳定协议，SSE 将在会话
Checkpoint 和断线恢复边界明确后接入。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent.supervisor import (
    SupervisorRequest,
    SupervisorRuntimeError,
    SupervisorSessionBusy,
    UnsupportedLegacyRequest,
)
from app.api.middleware.request_context import normalize_context_id
from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    conversation_thread_id,
)
from app.infrastructure.gateway_client import GatewayRequestContext

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class AgentChatRequest(BaseModel):
    """对话请求的稳定输入协议。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    message: str = Field(min_length=1, max_length=4000)
    locale: str = Field(default="zh-CN", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")


class AgentChatResponse(BaseModel):
    """对话响应，不暴露模型供应商对象或内部异常详情。"""

    conversation_id: str
    answer: str
    route: str
    tool_steps: int
    input_tokens: int | None
    output_tokens: int | None


@router.post("/chat", response_model=AgentChatResponse)
async def chat(
    payload: AgentChatRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> AgentChatResponse:
    """处理一次健身 Agent 对话请求。"""

    if not x_agent_context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signed agent context is required",
        )

    try:
        identity = request.app.state.context_verifier.verify(x_agent_context)
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signed agent context",
        ) from exc

    request_id = normalize_context_id(x_request_id) or str(uuid4())
    trace_id = normalize_context_id(x_trace_id) or request_id
    supervisor = request.app.state.supervisor
    try:
        response = await supervisor.invoke(
            SupervisorRequest(
                user_message=payload.message,
                gateway_context=GatewayRequestContext(
                    signed_context=x_agent_context,
                    request_id=request_id,
                    trace_id=trace_id,
                ),
                conversation_id=payload.conversation_id,
                thread_id=conversation_thread_id(payload.conversation_id, identity),
                locale=payload.locale,
                identity=identity,
            )
        )
    except UnsupportedLegacyRequest as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SupervisorSessionBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation is already being processed",
        ) from exc
    except SupervisorRuntimeError as exc:
        # 不把模型、Gateway、Prompt 或签名上下文详情返回给调用方；具体原因通过
        # request_id/trace_id 在结构化日志和 Trace 系统中定位。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent service is temporarily unavailable",
        ) from exc

    return AgentChatResponse(
        conversation_id=payload.conversation_id,
        answer=response.answer,
        route=response.route,
        tool_steps=response.tool_steps,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
