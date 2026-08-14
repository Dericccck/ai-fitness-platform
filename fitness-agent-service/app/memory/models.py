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

    def is_active(self, now: datetime) -> bool:
        """判断当前是否仍可作为训练计划上下文使用。"""

        return self.status == "ACTIVE" and (self.expires_at is None or self.expires_at > now)

    def to_prompt_line(self) -> str:
        """转换成受控、简短的 Prompt 上下文，不暴露数据库内部字段。"""

        value = self.content.get("value")
        unit = self.content.get("unit")
        suffix = f"{unit}" if isinstance(unit, str) and unit else ""
        return f"- {self.memory_type}/{self.memory_key}: {value}{suffix}"


def validate_memory_owner(identity: AgentIdentity, organization_id: str) -> None:
    """拒绝把 Memory 写入签名身份没有覆盖的机构。"""

    if not organization_id.strip() or organization_id not in identity.organization_ids:
        raise MemoryValidationError("memory organization is outside signed identity scope")
