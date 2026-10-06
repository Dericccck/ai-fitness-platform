"""主动式 Agent 候选和 Inbox 的运维治理 API。"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.proactive.candidate_repository import ProactiveCandidateRepository
from app.proactive.candidates import CandidateType, candidate_expiry
from app.proactive.repository import ProactiveEventRepository

router = APIRouter(prefix="/api/v1/admin/proactive", tags=["admin-proactive"])
_PLATFORM_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ADMIN", "SUPER_ADMIN"})
_ADMIN_ROLES = _PLATFORM_ADMIN_ROLES | {"ORGANIZATION_ADMIN"}


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=256)


class ProactiveSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str = Field(min_length=1, max_length=128)
    audience: str = Field(pattern=r"^(STUDENT|COACH|OPERATIONS)$")
    enabled: bool = True
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("必须提供有效的 IANA 时区") from exc
        return value


class ManualCandidateRequest(BaseModel):
    """为尚未接入领域事件的受控业务流程提供可靠入口。"""

    model_config = ConfigDict(extra="forbid")
    candidate_type: CandidateType
    organization_id: str = Field(min_length=1, max_length=128)
    subject_user_id: str = Field(min_length=1, max_length=128)
    aggregate_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    aggregate_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    source_reference: str = Field(min_length=1, max_length=200)
    facts: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ManualCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str | None
    deduplicated: bool


@router.post(
    "/candidates", response_model=ManualCandidateResponse, status_code=status.HTTP_201_CREATED
)
async def create_manual_candidate(
    payload: ManualCandidateRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> ManualCandidateResponse:
    identity = _admin(request, x_agent_context)
    if payload.organization_id not in identity.organization_ids and not _is_platform_admin(
        identity
    ):
        raise HTTPException(status_code=403, detail="无权为该机构创建主动候选")
    if len(payload.facts) > 30:
        raise HTTPException(status_code=422, detail="候选事实字段不能超过 30 个")
    repository = cast(ProactiveCandidateRepository, request.app.state.proactive_candidates)
    dedupe_key = f"manual:{payload.organization_id}:{payload.source_reference}"
    async with request.app.state.database.engine.begin() as connection:
        candidate_id = await repository.enqueue(
            connection,
            candidate_type=payload.candidate_type.value,
            reason_code=payload.reason_code,
            subject_user_id=payload.subject_user_id,
            organization_id=payload.organization_id,
            aggregate_type=payload.aggregate_type,
            aggregate_id=payload.aggregate_id,
            trigger_source="MANUAL",
            trigger_event_ids=(),
            facts=dict(payload.facts),
            dedupe_key=dedupe_key,
            expires_at=candidate_expiry(payload.candidate_type),
        )
    return ManualCandidateResponse(candidate_id=candidate_id, deduplicated=candidate_id is None)


@router.put("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_proactive_subscription(
    payload: ProactiveSubscriptionRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    if payload.organization_id not in identity.organization_ids and not _is_platform_admin(
        identity
    ):
        raise HTTPException(status_code=403, detail="无权配置该机构")
    repository = cast(ProactiveCandidateRepository, request.app.state.proactive_candidates)
    async with request.app.state.database.engine.begin() as connection:
        await repository.upsert_subscription(
            connection,
            subject_user_id=identity.subject,
            organization_id=payload.organization_id,
            audience=payload.audience,
            enabled=payload.enabled,
            timezone=payload.timezone,
        )


class DeadCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    candidate_type: str
    reason_code: str
    organization_id: str
    aggregate_type: str
    status: str
    attempt_count: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class DeadInboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    source: str
    event_type: str
    organization_id: str
    status: str
    attempt_count: int


@router.get("/candidates/dead", response_model=list[DeadCandidateResponse])
async def list_dead_candidates(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[DeadCandidateResponse]:
    identity = _admin(request, x_agent_context)
    repository = cast(ProactiveCandidateRepository, request.app.state.proactive_candidates)
    async with request.app.state.database.engine.connect() as connection:
        records = await repository.list_dead(
            connection, limit=limit, organization_ids=_organization_scope(identity)
        )
    return [
        DeadCandidateResponse(
            id=item.id,
            candidate_type=item.candidate_type,
            reason_code=item.reason_code,
            organization_id=item.organization_id,
            aggregate_type=item.aggregate_type,
            status=item.status,
            attempt_count=item.attempt_count,
            last_error_code=item.last_error_code,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in records
    ]


@router.post("/candidates/dead/{candidate_id}/replay", status_code=status.HTTP_204_NO_CONTENT)
async def replay_dead_candidate(
    payload: ReplayRequest,
    request: Request,
    candidate_id: str = Path(min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    repository = cast(ProactiveCandidateRepository, request.app.state.proactive_candidates)
    async with request.app.state.database.engine.begin() as connection:
        if not await repository.replay_dead(
            connection,
            candidate_id=candidate_id,
            operator_id=identity.subject,
            reason=payload.reason,
            organization_ids=_organization_scope(identity),
        ):
            raise HTTPException(status_code=409, detail="DEAD 候选不存在或已重放")


@router.get("/inbox/dead", response_model=list[DeadInboxResponse])
async def list_dead_inbox(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[DeadInboxResponse]:
    identity = _admin(request, x_agent_context)
    async with request.app.state.database.engine.connect() as connection:
        records = await ProactiveEventRepository().list_dead(
            connection, limit=limit, organization_ids=_organization_scope(identity)
        )
    return [
        DeadInboxResponse(
            event_id=item.event_id,
            source=item.source,
            event_type=item.event_type,
            organization_id=item.organization_id,
            status=item.status,
            attempt_count=item.attempt_count,
        )
        for item in records
    ]


@router.post("/inbox/dead/{event_id}/replay", status_code=status.HTTP_204_NO_CONTENT)
async def replay_dead_inbox(
    payload: ReplayRequest,
    request: Request,
    event_id: str = Path(min_length=1, max_length=200),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    async with request.app.state.database.engine.begin() as connection:
        if not await ProactiveEventRepository().replay_dead(
            connection,
            event_id=event_id,
            operator_id=identity.subject,
            reason=payload.reason,
            organization_ids=_organization_scope(identity),
        ):
            raise HTTPException(status_code=409, detail="DEAD Inbox 事件不存在或已重放")


def _admin(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(status_code=401, detail="必须提供已签名的 AgentContext")
    try:
        identity = cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(status_code=401, detail="已签名的 AgentContext 无效") from exc
    if not _ADMIN_ROLES.intersection(identity.roles):
        raise HTTPException(status_code=403, detail="需要管理员角色")
    return identity


def _is_platform_admin(identity: AgentIdentity) -> bool:
    return bool(_PLATFORM_ADMIN_ROLES.intersection(identity.roles))


def _organization_scope(identity: AgentIdentity) -> tuple[str, ...] | None:
    return None if _is_platform_admin(identity) else tuple(sorted(identity.organization_ids))
