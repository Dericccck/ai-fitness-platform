"""健身 Memory 的稳定领域模型和生命周期状态。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.infrastructure.agent_context import AgentIdentity

MemoryType = Literal[
    "TRAINING_GOAL",
    "TRAINING_PREFERENCE",
    "EQUIPMENT_AVAILABILITY",
    "SCHEDULE_PREFERENCE",
    "COMMUNICATION_PREFERENCE",
]
MemoryStatus = Literal["ACTIVE", "REVOKED", "EXPIRED"]
MemoryEventType = Literal["SAVED", "REVOKED", "EXPIRED", "REDACTED"]
MemoryEventActorType = Literal["AGENT", "USER", "SYSTEM"]


class MemoryValidationError(ValueError):
    """Memory 违反允许的业务范围、主体范围或生命周期约束。"""


@dataclass(frozen=True)
class FitnessMemory:
    """一条已经结构化的长期健身 Memory。

    ``content`` 只包含 ``key``、``value`` 和可选 ``unit``，避免把任意模型生成文本当作
    长期事实保存。``subject_user_id`` 始终来自签名 AgentIdentity，不能由模型传入。
    """

    id: str
    subject_user_id: str
    organization_id: str
    memory_type: MemoryType
    memory_key: str
    content: dict[str, Any]
    source_type: Literal["USER_EXPLICIT"]
    confidence: float
    status: MemoryStatus
    version: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # 正文脱敏后仍保留类型、稳定键和生命周期状态，供用户看到“这条 Memory 曾存在过”，
    # 但不能再恢复原始偏好。默认值兼容内存测试和历史调用方构造领域对象。
    content_redacted: bool = False

    def is_active(self, now: datetime) -> bool:
        """判断当前是否仍可作为训练计划上下文使用。"""

        return (
            self.status == "ACTIVE"
            and not self.content_redacted
            and (self.expires_at is None or self.expires_at > now)
        )

    def to_prompt_line(self) -> str:
        """转换成受控、简短的 Prompt 上下文，不暴露数据库内部字段。"""

        if self.content_redacted:
            return f"- {self.memory_type}/{self.memory_key}: [内容已按保留策略脱敏]"
        value = self.content.get("value")
        unit = self.content.get("unit")
        suffix = f"{unit}" if isinstance(unit, str) and unit else ""
        return f"- {self.memory_type}/{self.memory_key}: {value}{suffix}"


@dataclass(frozen=True)
class MemoryEventRecord:
    """正式 Memory 的生命周期审计摘要。

    事件表只记录状态变化和版本快照，不复制 Memory 正文。这样用户可以追踪某条
    记忆何时被确认保存、撤销或自动过期，同时避免审计日志扩大低敏用户偏好的泄露面。
    ``operation_id`` 还是写操作的幂等键，用于网络重试时复用同一结果。
    """

    id: int
    memory_id: str
    subject_user_id: str
    organization_id: str
    event_type: MemoryEventType
    actor_type: MemoryEventActorType
    actor_user_id: str | None
    status_after: MemoryStatus
    version_after: int
    request_id: str
    operation_id: str
    created_at: datetime


def validate_memory_owner(identity: AgentIdentity, organization_id: str) -> None:
    """拒绝把 Memory 写入签名身份没有覆盖的机构。"""

    if not organization_id.strip() or organization_id not in identity.organization_ids:
        raise MemoryValidationError("Memory 所属机构不在已签名身份的权限范围内")
