"""正式 Memory 的用户管理 API。

这里的撤销接口是用户在管理页面上主动点击确认后的明确写操作，因此接口本身已经
承担了确认边界，不再额外调用 LangGraph ``interrupt()``。对话内的
``fitness.memory.revoke.v1`` 仍然必须走原有的确认单和 ``interrupt()``。
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.middleware.request_context import normalize_context_id
from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentIdentity,
)
from app.memory.models import FitnessMemory, MemoryEventRecord, MemoryValidationError
from app.memory.repository import MemoryNotFoundError, MemoryVersionConflictError
from app.memory.service import MemoryService

router = APIRouter(prefix="/api/v1/agent/memories", tags=["agent-memories"])


class MemoryRevocationRequest(BaseModel):
    """用户明确点击撤销时提交的版本和稳定决定 ID。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    decision_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class MemoryResponse(BaseModel):
    """返回给用户管理页面的 Memory 视图，不暴露请求幂等字段。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    memory_type: str
    memory_key: str
    content: dict[str, object]
    status: str
    version: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryEventResponse(BaseModel):
    """生命周期事件摘要；不返回正文、请求参数或用户内部标识。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    event_type: str
    actor_type: str
    status_after: str
    version_after: int
    created_at: datetime


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> list[MemoryResponse]:
    """读取当前签名用户在指定机构内仍有效的正式 Memory。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        memories = await _memory_service(request).list_active(
            identity=identity, organization_id=organization_id
        )
    except MemoryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organization is forbidden"
        ) from exc
    return [_to_response(memory) for memory in memories]


@router.post("/{memory_id}/revocations", response_model=MemoryResponse)
async def revoke_memory(
    memory_id: str,
    payload: MemoryRevocationRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> MemoryResponse:
    """撤销本人 Memory；同一决定请求重试会返回同一已撤销结果。"""

    identity = _verify_identity(request, x_agent_context)
    # 决定 ID 来自用户界面，是本次业务动作的幂等键；内层前缀避免和保存操作冲突。
    operation_id = f"memory-api-revoke:{memory_id}:{payload.decision_request_id}"
    request_id = normalize_context_id(x_request_id) or operation_id
    try:
        # 机构 ID 不从请求体接收。先按签名主体读取目标 Memory，再把数据库返回的归属
        # 机构传入撤销 Service；多机构用户也因此不会获得跨机构猜测或越权能力。
        target = await _memory_service(request).get_for_subject(
            identity=identity, memory_id=memory_id
        )
        memory = await _memory_service(request).revoke(
            identity=identity,
            organization_id=target.organization_id,
            memory_id=memory_id,
            expected_version=payload.expected_version,
            source_request_id=operation_id,
            request_id=request_id,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    except MemoryVersionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="memory version changed or memory is no longer active",
        ) from exc
    except MemoryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="memory organization is forbidden"
        ) from exc
    return _to_response(memory)


@router.get("/{memory_id}/events", response_model=list[MemoryEventResponse])
async def list_memory_events(
    memory_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[MemoryEventResponse]:
    """读取当前用户本人 Memory 的生命周期审计摘要。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        events = await _memory_service(request).list_events(
            identity=identity, memory_id=memory_id, limit=limit
        )
    except (MemoryNotFoundError, MemoryValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    return [_to_event_response(event) for event in events]


def _memory_service(request: Request) -> MemoryService:
    return cast(MemoryService, request.app.state.memory_service)


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signed agent context is required",
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signed agent context",
        ) from exc


def _to_response(memory: FitnessMemory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        organization_id=memory.organization_id,
        memory_type=memory.memory_type,
        memory_key=memory.memory_key,
        content=memory.content,
        status=memory.status,
        version=memory.version,
        expires_at=memory.expires_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _to_event_response(event: MemoryEventRecord) -> MemoryEventResponse:
    return MemoryEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor_type=event.actor_type,
        status_after=event.status_after,
        version_after=event.version_after,
        created_at=event.created_at,
    )
