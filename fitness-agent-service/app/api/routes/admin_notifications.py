"""管理员通知模板版本和发布 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.notifications.outbox import (
    NotificationDeliveryAttemptRecord,
    NotificationOutboxRepository,
)
from app.notifications.templates import (
    NotificationTemplateEventRecord,
    NotificationTemplateRecord,
    NotificationTemplateRepository,
    NotificationTemplateValidationError,
)

router = APIRouter(prefix="/api/v1/admin/notifications", tags=["admin-notifications"])


class NotificationTemplateDraftRequest(BaseModel):
    """创建通知模板草稿；正文不允许模型自由写入，必须由管理员提交。"""

    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    channel: Literal["IN_APP"] = "IN_APP"
    title_template: str = Field(min_length=1, max_length=200)
    body_template: str = Field(min_length=1, max_length=2000)
    variables: tuple[str, ...] = Field(default=(), max_length=20)
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class NotificationTemplateOperationRequest(BaseModel):
    """审核或发布动作的幂等请求体。"""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class NotificationTemplateResponse(BaseModel):
    """管理员查看的模板状态，不返回数据库内部连接信息。"""

    model_config = ConfigDict(extra="forbid")

    template_key: str
    channel: str
    version: int
    status: str
    title_template: str
    body_template: str
    variables: tuple[str, ...]
    created_by: str
    approved_by: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationTemplateEventResponse(BaseModel):
    """模板生命周期审计摘要，不返回操作幂等键或模板正文。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    event_type: str
    actor_user_id: str
    status_after: str
    created_at: datetime


class NotificationDeliveryAttemptResponse(BaseModel):
    """通知投递运维摘要；不返回用户主体 ID、业务聚合 ID 或通知正文。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    outbox_id: str
    notification_type: str
    organization_id: str
    channel: str
    attempt_no: int
    status: str
    error_code: str | None
    provider_message_id: str | None
    started_at: datetime
    finished_at: datetime | None


@router.post("/templates", response_model=NotificationTemplateResponse)
async def create_notification_template_draft(
    payload: NotificationTemplateDraftRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> NotificationTemplateResponse:
    """创建新版本草稿；需要平台管理员签名身份。"""

    identity = _verify_platform_admin(request, x_agent_context)
    repository = _repository(request)
    try:
        async with request.app.state.database.engine.begin() as connection:
            template = await repository.create_draft(
                connection,
                template_key=payload.template_key,
                channel=payload.channel,
                title_template=payload.title_template,
                body_template=payload.body_template,
                variables=payload.variables,
                created_by=identity.subject,
                operation_id=payload.operation_id,
            )
    except NotificationTemplateValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _to_response(template)


@router.post(
    "/templates/{template_key}/{channel}/{version}/approve",
    response_model=NotificationTemplateResponse,
)
async def approve_notification_template(
    payload: NotificationTemplateOperationRequest,
    request: Request,
    template_key: str = Path(min_length=1, max_length=128),
    channel: Literal["IN_APP"] = Path(...),
    version: int = Path(ge=1),
    x_agent_context: str | None = Header(default=None),
) -> NotificationTemplateResponse:
    """审核模板草稿；审核人和创建人都来自签名上下文且不能是同一人。"""

    identity = _verify_platform_admin(request, x_agent_context)
    try:
        async with request.app.state.database.engine.begin() as connection:
            template = await _repository(request).approve(
                connection,
                template_key=template_key,
                channel=channel,
                version=version,
                approved_by=identity.subject,
                operation_id=payload.operation_id,
            )
    except NotificationTemplateValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(template)


@router.post(
    "/templates/{template_key}/{channel}/{version}/publish",
    response_model=NotificationTemplateResponse,
)
async def publish_notification_template(
    payload: NotificationTemplateOperationRequest,
    request: Request,
    template_key: str = Path(min_length=1, max_length=128),
    channel: Literal["IN_APP"] = Path(...),
    version: int = Path(ge=1),
    x_agent_context: str | None = Header(default=None),
) -> NotificationTemplateResponse:
    """发布已审核模板；旧的同键发布版本会被退役。"""

    identity = _verify_platform_admin(request, x_agent_context)
    try:
        async with request.app.state.database.engine.begin() as connection:
            template = await _repository(request).publish(
                connection,
                template_key=template_key,
                channel=channel,
                version=version,
                published_by=identity.subject,
                operation_id=payload.operation_id,
            )
    except NotificationTemplateValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(template)


@router.get(
    "/templates/{template_key}/{channel}/{version}/events",
    response_model=list[NotificationTemplateEventResponse],
)
async def list_notification_template_events(
    request: Request,
    template_key: str = Path(min_length=1, max_length=128),
    channel: Literal["IN_APP"] = Path(...),
    version: int = Path(ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[NotificationTemplateEventResponse]:
    """读取模板生命周期摘要，供管理员排查发布状态。"""

    _verify_platform_admin(request, x_agent_context)
    try:
        async with request.app.state.database.engine.connect() as connection:
            events = await _repository(request).list_events(
                connection,
                template_key=template_key,
                channel=channel,
                version=version,
                limit=limit,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [_to_event_response(event) for event in events]


@router.get(
    "/delivery-attempts",
    response_model=list[NotificationDeliveryAttemptResponse],
)
async def list_notification_delivery_attempts(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    notification_type: str | None = Query(default=None, min_length=1, max_length=128),
    channel: Literal["IN_APP"] | None = Query(default=None),
    delivery_status: Literal["STARTED", "SUCCEEDED", "RETRYABLE_FAILED", "FINAL_FAILED"]
    | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[NotificationDeliveryAttemptResponse]:
    """读取通知投递摘要，供平台管理员排查重试和最终失败。

    返回结果刻意隐藏用户主体 ID、业务聚合 ID、标题和正文；管理员只能通过固定的
    通知类型、机构和错误码定位基础设施问题，不能把运维接口当成业务数据查询入口。
    """

    _verify_platform_admin(request, x_agent_context)
    try:
        async with request.app.state.database.engine.connect() as connection:
            attempts = await _outbox_repository(request).list_delivery_attempts(
                connection,
                organization_id=organization_id,
                notification_type=notification_type,
                channel=channel,
                status=delivery_status,
                limit=limit,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [_to_delivery_attempt_response(attempt) for attempt in attempts]


def _repository(request: Request) -> NotificationTemplateRepository:
    return cast(NotificationTemplateRepository, request.app.state.notification_templates)


def _outbox_repository(request: Request) -> NotificationOutboxRepository:
    return cast(NotificationOutboxRepository, request.app.state.notification_outbox)


def _verify_platform_admin(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signed agent context is required"
        )
    try:
        identity = cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signed agent context"
        ) from exc
    if not {"SYSTEM_ADMIN", "ADMIN", "SUPER_ADMIN"}.intersection(identity.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform admin role is required"
        )
    return identity


def _to_response(template: NotificationTemplateRecord) -> NotificationTemplateResponse:
    return NotificationTemplateResponse(
        template_key=template.template_key,
        channel=template.channel,
        version=template.version,
        status=template.status,
        title_template=template.title_template,
        body_template=template.body_template,
        variables=template.variables,
        created_by=template.created_by,
        approved_by=template.approved_by,
        published_at=template.published_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _to_event_response(event: NotificationTemplateEventRecord) -> NotificationTemplateEventResponse:
    return NotificationTemplateEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        status_after=event.status_after,
        created_at=event.created_at,
    )


def _to_delivery_attempt_response(
    attempt: NotificationDeliveryAttemptRecord,
) -> NotificationDeliveryAttemptResponse:
    return NotificationDeliveryAttemptResponse(
        id=attempt.id,
        outbox_id=attempt.outbox_id,
        notification_type=attempt.notification_type,
        organization_id=attempt.organization_id,
        channel=attempt.channel,
        attempt_no=attempt.attempt_no,
        status=attempt.status,
        error_code=attempt.error_code,
        provider_message_id=attempt.provider_message_id,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
    )
