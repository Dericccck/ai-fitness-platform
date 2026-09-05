"""Agent 写操作确认单查询和决定 API。

这里是用户确认动作的 HTTP 边界。接口只读取确认单的脱敏摘要；批准时先持久化决定，
再由服务端恢复对应 LangGraph thread，使用确认单中的加密参数和短时凭证调用 Gateway。
浏览器不会接触精确参数、确认 Token 或 ``Command(resume=...)``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent.supervisor import SupervisorRuntimeError, SupervisorSessionBusy
from app.confirmation.models import ConfirmationRecord, ConfirmationStateError
from app.confirmation.repository import ConfirmationNotFound
from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext

router = APIRouter(prefix="/api/v1/agent/confirmations", tags=["agent-confirmations"])


class ConfirmationDecisionRequest(BaseModel):
    """确认决定输入；主体、组织和角色只能来自签名 AgentContext。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    # 决定请求和最初业务 request_id 分离，避免不同阶段的重试相互覆盖。
    decision_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class ConfirmationRevocationRequest(BaseModel):
    """确认撤销输入；撤销幂等键与批准决定键分离。"""

    model_config = ConfigDict(extra="forbid")

    revocation_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class ConfirmationReconcileRequest(BaseModel):
    """人工对账入口目前只允许把 RUNNING 明确标为 UNKNOWN。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=256)


class ConfirmationResponse(BaseModel):
    """供确认卡片和页面刷新使用的脱敏确认单状态。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    protocol_version: int
    organization_id: str
    tool_id: str
    risk_level: str
    action: str
    resource_type: str
    resource_id: str | None
    expected_resource_version: int | None
    display_summary: dict[str, object]
    authorization_status: str
    execution_status: str
    version: int
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None
    cancelled_at: datetime | None
    execution_started_at: datetime | None
    finished_at: datetime | None
    decision_request_id: str | None
    last_error_code: str | None


@router.get("/{confirmation_id}", response_model=ConfirmationResponse)
async def get_confirmation(
    confirmation_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> ConfirmationResponse:
    """读取当前主体可见的确认单，支持页面刷新和断线重连。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        record = await request.app.state.confirmation_service.get_for_subject(
            confirmation_id, identity
        )
    except (ConfirmationNotFound, ConfirmationStateError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到确认单") from exc
    return _to_response(record)


@router.post("/{confirmation_id}/decisions", response_model=ConfirmationResponse)
async def decide_confirmation(
    confirmation_id: str,
    payload: ConfirmationDecisionRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> ConfirmationResponse:
    """批准或拒绝确认单；批准会继续服务端恢复，重复请求仍由决定 ID 幂等收敛。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        record = await request.app.state.confirmation_service.decide(
            confirmation_id,
            identity=identity,
            decision=payload.decision,
            decision_request_id=payload.decision_request_id,
            trace_id=x_trace_id,
        )
    except ConfirmationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到确认单") from exc
    except ConfirmationStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if payload.decision == "REJECT":
        return _to_response(record)

    # 已经执行成功的相同批准重试直接返回最终事实，不能再次恢复图或重复调用 Gateway。
    if record.execution_status in {"SUCCEEDED", "FAILED_FINAL"}:
        return _to_response(record)
    if record.execution_status == "RUNNING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已确认的操作正在执行",
        )

    # 批准后由服务端使用同一 thread 恢复图；前端不提交 thread、参数或 Token。
    supervisor = request.app.state.supervisor
    try:
        await supervisor.resume_confirmation(
            confirmation_id,
            identity=identity,
            gateway_context=GatewayRequestContext(
                signed_context=x_agent_context or "",
                request_id=payload.decision_request_id,
                trace_id=x_trace_id or payload.decision_request_id,
            ),
            thread_id=record.thread_id,
        )
    except SupervisorSessionBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="会话正在处理中") from exc
    except SupervisorRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="已确认的操作暂时不可用",
        ) from exc
    return _to_response(
        await request.app.state.confirmation_service.get_for_subject(confirmation_id, identity)
    )


@router.post("/{confirmation_id}/revocations", response_model=ConfirmationResponse)
async def revoke_confirmation(
    confirmation_id: str,
    payload: ConfirmationRevocationRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> ConfirmationResponse:
    """撤销尚未执行的确认单；重复撤销请求按幂等键返回同一事实。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        record = await request.app.state.confirmation_service.revoke(
            confirmation_id,
            identity=identity,
            revocation_request_id=payload.revocation_request_id,
            trace_id=x_trace_id,
        )
    except ConfirmationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到确认单") from exc
    except (ConfirmationStateError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(record)


@router.post("/{confirmation_id}/reconcile", response_model=ConfirmationResponse)
async def reconcile_confirmation(
    confirmation_id: str,
    payload: ConfirmationReconcileRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> ConfirmationResponse:
    """受控人工入口：结果未核实前只能标记 UNKNOWN，禁止伪造成功或失败。"""

    identity = _verify_identity(request, x_agent_context)
    if not ({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN"} & identity.roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有管理员可以执行对账")
    try:
        record = await request.app.state.confirmation_service.get_for_subject(
            confirmation_id, identity
        )
        if record.execution_status != "RUNNING":
            return _to_response(record)
        # 先查下游稳定操作 ID；只有确认下游成功才补写 SUCCEEDED。
        reconciled = await request.app.state.confirmation_service.reconcile_execution(
            confirmation_id,
            identity=identity,
            gateway_context=GatewayRequestContext(
                signed_context=x_agent_context or "",
                request_id=record.request_id,
                trace_id=x_trace_id,
            ),
            trace_id=x_trace_id,
        )
        if reconciled.execution_status == "SUCCEEDED":
            return _to_response(reconciled)
        reconciled = await request.app.state.confirmation_service.mark_execution_unknown(
            confirmation_id, trace_id=x_trace_id or payload.reason
        )
    except (ConfirmationNotFound, ConfirmationStateError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="确认单当前不可对账") from exc
    return _to_response(reconciled)


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="必须提供已签名的 AgentContext"
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="已签名的 AgentContext 无效"
        ) from exc


def _to_response(record: ConfirmationRecord) -> ConfirmationResponse:
    """只映射可展示字段，刻意不返回 payload_ciphertext、payload_hash 或凭证 JTI。"""

    return ConfirmationResponse(
        id=record.id,
        protocol_version=record.protocol_version,
        organization_id=record.organization_id,
        tool_id=record.tool_id,
        risk_level=record.risk_level,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        expected_resource_version=record.expected_resource_version,
        display_summary=dict(record.display_summary),
        authorization_status=record.authorization_status,
        execution_status=record.execution_status,
        version=record.version,
        created_at=record.created_at,
        expires_at=record.expires_at,
        approved_at=record.approved_at,
        rejected_at=record.rejected_at,
        cancelled_at=record.cancelled_at,
        execution_started_at=record.execution_started_at,
        finished_at=record.finished_at,
        decision_request_id=record.decision_request_id,
        last_error_code=record.last_error_code,
    )
