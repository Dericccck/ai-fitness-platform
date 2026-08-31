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
from app.memory.candidate import MemoryCandidateEventRecord, MemoryCandidateRecord
from app.memory.candidate_repository import MemoryCandidateNotFound, MemoryCandidateStateError
from app.memory.candidate_service import (
    MemoryCandidatePersistenceError,
    MemoryCandidateService,
)
from app.memory.models import MemoryValidationError
from app.notifications.outbox import InAppNotificationRecord, NotificationOutboxRepository

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


class MemoryCandidateEventResponse(BaseModel):
    """候选审计摘要；隐藏请求 ID、正文摘要和主体内部字段。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    event_type: str
    actor_type: str
    status_after: str
    created_at: datetime


class MemoryCandidateInboxItem(BaseModel):
    """审批收件箱中的一条待确认候选和对应站内通知。"""

    model_config = ConfigDict(extra="forbid")

    candidate: MemoryCandidateResponse
    notification_id: str | None
    notification_status: str | None
    notification_created_at: datetime | None


class MemoryCandidateInboxResponse(BaseModel):
    """供学员端直接渲染的候选审批收件箱。"""

    model_config = ConfigDict(extra="forbid")

    organization_id: str
    items: list[MemoryCandidateInboxItem]


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该机构") from exc
    return [_to_response(record) for record in records]


@router.get("/inbox", response_model=MemoryCandidateInboxResponse)
async def get_memory_candidate_inbox(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> MemoryCandidateInboxResponse:
    """聚合候选和站内通知，减少前端审批页的跨接口拼装。

    该接口是只读收件箱，不会因为打开页面而批准、拒绝或自动标记通知已读。真正的
    决定仍必须调用 ``/{candidate_id}/decisions``，由候选服务执行主体校验、幂等和
    ``PENDING -> APPROVED/REJECTED`` 状态转换。通知缺失时候选仍然返回，避免 Outbox
    发布延迟导致用户永远看不到待确认内容。
    """

    identity = _verify_identity(request, x_agent_context)
    service = _candidate_service(request)
    try:
        candidates = await service.list_pending(
            identity=identity,
            organization_id=organization_id,
            limit=limit,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该机构") from exc

    notification_repository = _notification_repository(request)
    async with request.app.state.database.engine.connect() as connection:
        notifications = await notification_repository.list_in_app(
            connection,
            subject_user_id=identity.subject,
            organization_id=organization_id,
            status=None,
            limit=limit,
        )
    notification_by_candidate = {
        notification.aggregate_id: notification
        for notification in notifications
        if notification.aggregate_type == "memory_candidate"
    }
    return MemoryCandidateInboxResponse(
        organization_id=organization_id,
        items=[
            _to_inbox_item(record, notification_by_candidate.get(record.id))
            for record in candidates
        ],
    )


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
            status_code=status.HTTP_404_NOT_FOUND, detail="未找到 Memory 候选"
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
            detail="Memory 候选暂时不可用",
        ) from exc
    return _to_response(result.candidate, memory_id=result.memory.id if result.memory else None)


@router.get("/{candidate_id}/events", response_model=list[MemoryCandidateEventResponse])
async def list_memory_candidate_events(
    candidate_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[MemoryCandidateEventResponse]:
    """读取当前用户本人候选的生命周期摘要。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        events = await _candidate_service(request).list_events(
            candidate_id, identity=identity, limit=limit
        )
    except MemoryCandidateNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="未找到 Memory 候选"
        ) from exc
    return [_to_event_response(event) for event in events]


def _candidate_service(request: Request) -> MemoryCandidateService:
    return cast(MemoryCandidateService, request.app.state.memory_candidate_service)


def _notification_repository(request: Request) -> NotificationOutboxRepository:
    return cast(NotificationOutboxRepository, request.app.state.notification_outbox)


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="必须提供已签名的 AgentContext",
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="已签名的 AgentContext 无效",
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


def _to_event_response(event: MemoryCandidateEventRecord) -> MemoryCandidateEventResponse:
    return MemoryCandidateEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor_type=event.actor_type,
        status_after=event.status_after,
        created_at=event.created_at,
    )


def _to_inbox_item(
    record: MemoryCandidateRecord,
    notification: InAppNotificationRecord | None,
) -> MemoryCandidateInboxItem:
    return MemoryCandidateInboxItem(
        candidate=_to_response(record),
        notification_id=notification.id if notification else None,
        notification_status=notification.status if notification else None,
        notification_created_at=notification.created_at if notification else None,
    )
