"""管理员通知模板版本和发布 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.notifications.templates import (
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
    request: Request,
    template_key: str = Path(min_length=1, max_length=128),
    channel: Literal["IN_APP"] = Path("IN_APP"),
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
            )
    except NotificationTemplateValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(template)


@router.post(
    "/templates/{template_key}/{channel}/{version}/publish",
    response_model=NotificationTemplateResponse,
)
async def publish_notification_template(
    request: Request,
    template_key: str = Path(min_length=1, max_length=128),
    channel: Literal["IN_APP"] = Path("IN_APP"),
    version: int = Path(ge=1),
    x_agent_context: str | None = Header(default=None),
) -> NotificationTemplateResponse:
    """发布已审核模板；旧的同键发布版本会被退役。"""

    _verify_platform_admin(request, x_agent_context)
    try:
        async with request.app.state.database.engine.begin() as connection:
            template = await _repository(request).publish(
                connection,
                template_key=template_key,
                channel=channel,
                version=version,
            )
    except NotificationTemplateValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(template)


def _repository(request: Request) -> NotificationTemplateRepository:
    return cast(NotificationTemplateRepository, request.app.state.notification_templates)


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
