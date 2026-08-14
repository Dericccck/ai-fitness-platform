"""Agent 站内通知查询和已读 API。"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentIdentity,
)
from app.notifications.outbox import InAppNotificationRecord, NotificationOutboxRepository
from app.notifications.preferences import (
    DEFAULT_NOTIFICATION_TYPE,
    NotificationPreferenceRecord,
    NotificationPreferenceRepository,
    NotificationPreferenceValidationError,
)

router = APIRouter(prefix="/api/v1/agent/notifications", tags=["agent-notifications"])


class InAppNotificationResponse(BaseModel):
    """用户可见的通知摘要，不返回 Outbox payload 或内部请求标识。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    notification_type: str
    aggregate_type: str
    aggregate_id: str
    organization_id: str
    status: str
    created_at: datetime
    read_at: datetime | None


class NotificationPreferenceRequest(BaseModel):
    """用户显式保存的通知授权和发送窗口。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    quiet_start: time | None = None
    quiet_end: time | None = None
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    minimum_interval_seconds: int = Field(default=0, ge=0, le=604800)

    @model_validator(mode="after")
    def validate_quiet_window(self) -> NotificationPreferenceRequest:
        if (self.quiet_start is None) != (self.quiet_end is None):
            raise ValueError("quiet_start and quiet_end must be configured together")
        if self.quiet_start is not None and self.quiet_start == self.quiet_end:
            raise ValueError("quiet window cannot be zero length")
        return self


class NotificationPreferenceResponse(BaseModel):
    """通知偏好安全视图；不返回内部主体标识或数据库版本。"""

    model_config = ConfigDict(extra="forbid")

    organization_id: str
    notification_type: str
    enabled: bool
    quiet_start: time | None
    quiet_end: time | None
    timezone: str
    minimum_interval_seconds: int
    updated_at: datetime | None


@router.get("/preferences", response_model=NotificationPreferenceResponse)
async def get_notification_preference(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    notification_type: Literal["MEMORY_CANDIDATE_PENDING"] = Query(
        default=DEFAULT_NOTIFICATION_TYPE
    ),
    x_agent_context: str | None = Header(default=None),
) -> NotificationPreferenceResponse:
    """读取本人通知偏好；未配置时返回默认允许策略。"""

    identity = _verify_identity(request, x_agent_context)
    _require_organization(identity, organization_id)
    repository = _preference_repository(request)
    async with request.app.state.database.engine.connect() as connection:
        preference = await repository.get(
            connection,
            subject_user_id=identity.subject,
            organization_id=organization_id,
            notification_type=notification_type,
        )
    return _to_preference_response(preference)


@router.put("/preferences", response_model=NotificationPreferenceResponse)
async def save_notification_preference(
    payload: NotificationPreferenceRequest,
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    notification_type: Literal["MEMORY_CANDIDATE_PENDING"] = Query(
        default=DEFAULT_NOTIFICATION_TYPE
    ),
    x_agent_context: str | None = Header(default=None),
) -> NotificationPreferenceResponse:
    """保存本人通知偏好；这是设置页面的明确操作，不经过 Agent 工具确认。"""

    identity = _verify_identity(request, x_agent_context)
    _require_organization(identity, organization_id)
    repository = _preference_repository(request)
    try:
        async with request.app.state.database.engine.begin() as connection:
            preference = await repository.upsert(
                connection,
                subject_user_id=identity.subject,
                organization_id=organization_id,
                notification_type=notification_type,
                enabled=payload.enabled,
                quiet_start=payload.quiet_start,
                quiet_end=payload.quiet_end,
                timezone=payload.timezone,
                minimum_interval_seconds=payload.minimum_interval_seconds,
            )
    except NotificationPreferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _to_preference_response(preference)


@router.get("", response_model=list[InAppNotificationResponse])
async def list_notifications(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=128),
    notification_status: Literal["UNREAD", "READ", "DISMISSED"] | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    x_agent_context: str | None = Header(default=None),
) -> list[InAppNotificationResponse]:
    """读取当前签名用户在指定机构下的站内通知。"""

    identity = _verify_identity(request, x_agent_context)
    _require_organization(identity, organization_id)
    repository = _repository(request)
    async with request.app.state.database.engine.connect() as connection:
        notifications = await repository.list_in_app(
            connection,
            subject_user_id=identity.subject,
            organization_id=organization_id,
            status=notification_status,
            limit=limit,
        )
    return [_to_response(notification) for notification in notifications]


@router.post("/{notification_id}/read", response_model=InAppNotificationResponse)
async def mark_notification_read(
    notification_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> InAppNotificationResponse:
    """将当前用户本人通知标记为已读；重复调用保持幂等。"""

    identity = _verify_identity(request, x_agent_context)
    repository = _repository(request)
    # 已读是用户界面状态，不改变 Memory、训练计划或业务事实，因此不需要 Agent 确认单。
    async with request.app.state.database.engine.begin() as connection:
        notification = await repository.mark_in_app_read(
            connection,
            notification_id=notification_id,
            subject_user_id=identity.subject,
            organization_ids=list(identity.organization_ids),
        )
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")
    return _to_response(notification)


def _repository(request: Request) -> NotificationOutboxRepository:
    return cast(NotificationOutboxRepository, request.app.state.notification_outbox)


def _preference_repository(request: Request) -> NotificationPreferenceRepository:
    return cast(NotificationPreferenceRepository, request.app.state.notification_preferences)


def _require_organization(identity: AgentIdentity, organization_id: str) -> None:
    if organization_id not in identity.organization_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organization is forbidden"
        )


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


def _to_response(notification: InAppNotificationRecord) -> InAppNotificationResponse:
    return InAppNotificationResponse(
        id=notification.id,
        notification_type=notification.notification_type,
        aggregate_type=notification.aggregate_type,
        aggregate_id=notification.aggregate_id,
        organization_id=notification.organization_id,
        status=notification.status,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


def _to_preference_response(
    preference: NotificationPreferenceRecord,
) -> NotificationPreferenceResponse:
    return NotificationPreferenceResponse(
        organization_id=preference.organization_id,
        notification_type=preference.notification_type,
        enabled=preference.enabled,
        quiet_start=preference.quiet_start,
        quiet_end=preference.quiet_end,
        timezone=preference.timezone,
        minimum_interval_seconds=preference.minimum_interval_seconds,
        updated_at=preference.updated_at,
    )
