"""通知渠道适配器边界。

Worker 只负责领取 Outbox、执行通知策略和记录投递状态；具体渠道通过适配器完成。
当前实现站内通知，未来 RabbitMQ、短信和 Push 只需实现同一接口，不把供应商 SDK
渗透到 Memory、候选或通知 Outbox 事务中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .outbox import NotificationOutboxRecord, NotificationOutboxRepository


@dataclass(frozen=True)
class NotificationDeliveryRequest:
    """一次渠道投递所需的已渲染快照，不允许适配器自行调用 LLM。"""

    record: NotificationOutboxRecord
    template_version: int
    title: str
    body: str


@dataclass(frozen=True)
class NotificationDeliveryReceipt:
    """渠道返回的稳定投递凭证；站内通知使用收件箱 ID。"""

    channel: str
    provider_message_id: str


class NotificationChannelAdapter(Protocol):
    """统一渠道接口；实现必须保证自身幂等或使用 Outbox 去重键。"""

    channel: str

    async def deliver(
        self,
        connection: object,
        request: NotificationDeliveryRequest,
    ) -> NotificationDeliveryReceipt: ...


class InAppNotificationChannelAdapter:
    """把通知写入当前用户站内收件箱，不依赖外部供应商。"""

    channel = "IN_APP"

    def __init__(self, repository: NotificationOutboxRepository) -> None:
        self.repository = repository

    async def deliver(
        self,
        connection: object,
        request: NotificationDeliveryRequest,
    ) -> NotificationDeliveryReceipt:
        notification_id = await self.repository.write_in_app_notification(
            connection,
            record=request.record,
            template_version=request.template_version,
            title=request.title,
            body=request.body,
        )
        return NotificationDeliveryReceipt(
            channel=self.channel,
            provider_message_id=notification_id,
        )
