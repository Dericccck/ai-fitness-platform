"""主动提醒使用的跨服务事件契约和路由规则。

事件只携带业务事实 ID 和必要的接收人 ID，不携带模型生成的通知正文。正文由通知模板
控制面在后续 IN_APP 投递时渲染，避免 RabbitMQ 消息成为未审计的内容注入入口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

SUPPORTED_PROACTIVE_EVENT_TYPES = frozenset(
    {
        "APPOINTMENT_CREATED",
        "APPOINTMENT_RESCHEDULED",
        "APPOINTMENT_CANCELLED",
        "TRAINING_PLAN_PUBLISHED",
        "TRAINING_PLAN_REVIEW_REQUIRED",
    }
)
SUPPORTED_PROACTIVE_EVENT_SOURCES = frozenset({"booking", "training"})


class ProactiveEventContractError(ValueError):
    """跨服务事件不符合主动提醒契约。"""


class ProactiveEventMessage(BaseModel):
    """RabbitMQ 中传输的标准事件信封。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("event_id", "eventId"),
    )
    source: str = Field(default="booking", min_length=1, max_length=64)
    event_type: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("event_type", "eventType"),
    )
    aggregate_id: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("aggregate_id", "aggregateId"),
    )
    organization_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("organization_id", "organizationId"),
    )
    contract_version: int = Field(default=1, ge=1, le=100, validation_alias=AliasChoices("contract_version", "contractVersion"))
    aggregate_version: int | None = Field(default=None, ge=1, validation_alias=AliasChoices("aggregate_version", "aggregateVersion"))
    occurred_at: datetime | None = Field(default=None, validation_alias=AliasChoices("occurred_at", "occurredAt"))
    payload: dict[str, Any]

    @classmethod
    def from_json(cls, raw: bytes) -> ProactiveEventMessage:
        """解析 JSON，并把所有契约错误统一转换为可观测的业务异常。"""

        try:
            value = json.loads(raw.decode("utf-8"))
            message = cls.model_validate(value)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise ProactiveEventContractError("主动事件信封无效") from exc
        if message.event_type not in SUPPORTED_PROACTIVE_EVENT_TYPES:
            raise ProactiveEventContractError(f"不支持的主动事件类型：{message.event_type}")
        if message.source not in SUPPORTED_PROACTIVE_EVENT_SOURCES:
            raise ProactiveEventContractError(f"不支持的主动事件来源：{message.source}")
        return message


@dataclass(frozen=True)
class ProactiveNotificationTarget:
    """一个事件对应的站内通知接收目标。"""

    user_id: str
    role: str


def notification_targets(event: ProactiveEventMessage) -> tuple[ProactiveNotificationTarget, ...]:
    """根据受信任业务事件计算接收人，禁止事件自行指定任意通知正文。

    预约事件同时通知学员和教练；训练计划事件预留给后续 Training Service 事件发布器。
    目标 ID 为空或同一用户重复出现时直接拒绝，避免静默丢提醒或重复写入收件箱。
    """

    if event.event_type in {
        "APPOINTMENT_CREATED",
        "APPOINTMENT_RESCHEDULED",
        "APPOINTMENT_CANCELLED",
    }:
        candidates: tuple[ProactiveNotificationTarget, ...] = (
            ProactiveNotificationTarget(
                _required_id(event.payload, "studentId", "student_id"), "STUDENT"
            ),
            ProactiveNotificationTarget(
                _required_id(event.payload, "coachId", "coach_id"), "COACH"
            ),
        )
    elif event.event_type == "TRAINING_PLAN_PUBLISHED":
        candidates = (
            ProactiveNotificationTarget(
                _required_id(event.payload, "studentId", "student_id"), "STUDENT"
            ),
        )
    else:
        candidates = (
            ProactiveNotificationTarget(
                _required_id(event.payload, "coachId", "coach_id"), "COACH"
            ),
        )
    targets: list[ProactiveNotificationTarget] = []
    seen: set[str] = set()
    for target in candidates:
        if target.user_id in seen:
            continue
        seen.add(target.user_id)
        targets.append(target)
    if not targets:
        raise ProactiveEventContractError("主动事件没有通知目标")
    return tuple(targets)


def _required_id(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ProactiveEventContractError("主动事件缺少接收者 ID")
