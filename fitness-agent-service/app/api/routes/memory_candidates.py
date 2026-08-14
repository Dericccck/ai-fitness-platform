"""Memory 候选的本人查询和明确决定 API。

模型只能提出候选，不能通过对话参数直接把它变成长期事实。前端确认卡或管理页面
调用本路由时，Agent 服务会重新验证签名主体；批准路径先写入正式 ACTIVE Memory，
再把候选置为 APPROVED，重复请求使用决定请求 ID 和候选 ID 幂等收敛。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentIdentity,
)
from app.memory.candidate import MemoryCandidateRecord
from app.memory.candidate_repository import MemoryCandidateNotFound, MemoryCandidateStateError
from app.memory.candidate_service import (
    MemoryCandidatePersistenceError,
    MemoryCandidateService,
)
from app.memory.models import MemoryValidationError

router = APIRouter(
    prefix="/api/v1/agent/memory-candidates",
    tags=["agent-memory-candidates"],
)


class MemoryCandidateDecisionRequest(BaseModel):
    """用户对候选做出的明确决定；不接受模型生成的隐式批准。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    decision_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class MemoryCandidateResponse(BaseModel):
    """返回给用户界面的候选脱敏视图，不包含密文、摘要和内部主体字段。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    memory_type: str
    memory_key: str
    value: str
    unit: str | None
    status: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    decision_request_id: str | None
    decided_at: datetime | None
    memory_id: str | None = None


@router.get("", response_model=list[MemoryCandidateResponse])
async def list_memory_candidates(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[MemoryCandidateResponse]:
    """读取当前签名用户在指定机构内仍待确认的候选。"""

    identity = _verify_identity(request, x_agent_context)
    service = _candidate_service(request)
    try:
        records = await service.list_pending(
            identity=identity,
            organization_id=organization_id,
            limit=limit,
        )
    except MemoryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organization is forbidden"
        ) from exc
    return [_to_response(record) for record in records]


@router.post("/{candidate_id}/decisions", response_model=MemoryCandidateResponse)
async def decide_memory_candidate(
    candidate_id: str,
    payload: MemoryCandidateDecisionRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> MemoryCandidateResponse:
    """批准或拒绝候选；批准成功后响应中返回正式 Memory ID。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        result = await _candidate_service(request).decide(
            candidate_id,
            identity=identity,
            decision=payload.decision,
            decision_request_id=payload.decision_request_id,
        )
    except MemoryCandidateNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory candidate not found"
        ) from exc
    except MemoryCandidateStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MemoryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except MemoryCandidatePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory candidate is temporarily unavailable",
        ) from exc
    return _to_response(result.candidate, memory_id=result.memory.id if result.memory else None)


def _candidate_service(request: Request) -> MemoryCandidateService:
    return cast(MemoryCandidateService, request.app.state.memory_candidate_service)


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


def _to_response(
    record: MemoryCandidateRecord, *, memory_id: str | None = None
) -> MemoryCandidateResponse:
    """只暴露用户需要确认的字段，密文和安全审计字段留在服务端。"""

    return MemoryCandidateResponse(
        id=record.id,
        organization_id=record.organization_id,
        memory_type=record.candidate.memory_type,
        memory_key=record.candidate.memory_key,
        value=record.candidate.value,
        unit=record.candidate.unit,
        status=record.status,
        expires_at=record.expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        decision_request_id=record.decision_request_id,
        decided_at=record.decided_at,
        memory_id=memory_id,
    )
