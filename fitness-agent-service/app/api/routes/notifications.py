"""Agent 站内通知查询和已读 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentIdentity,
)
from app.notifications.outbox import InAppNotificationRecord, NotificationOutboxRepository

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
    if organization_id not in identity.organization_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organization is forbidden"
        )
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
