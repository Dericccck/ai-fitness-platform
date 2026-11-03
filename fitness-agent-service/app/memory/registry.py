"""受控的长期记忆维度与保守的身份规范化。"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .models import MemoryValidationError


@dataclass(frozen=True)
class SlotDefinition:
    memory_type: str
    cardinality: Literal["SINGLE", "SET"]


SLOT_REGISTRY: dict[str, SlotDefinition] = {
    "goals": SlotDefinition("TRAINING_GOAL", "SET"),
    "primary_goal": SlotDefinition("TRAINING_GOAL", "SINGLE"),
    "exercise_preference": SlotDefinition("TRAINING_PREFERENCE", "SET"),
    "training_environment": SlotDefinition("TRAINING_PREFERENCE", "SET"),
    "intensity": SlotDefinition("TRAINING_PREFERENCE", "SINGLE"),
    "available_equipment": SlotDefinition("EQUIPMENT_AVAILABILITY", "SET"),
    "available_windows": SlotDefinition("SCHEDULE_PREFERENCE", "SET"),
    "preferred_windows": SlotDefinition("SCHEDULE_PREFERENCE", "SET"),
    "session_duration": SlotDefinition("SCHEDULE_PREFERENCE", "SINGLE"),
    "language": SlotDefinition("COMMUNICATION_PREFERENCE", "SINGLE"),
    "verbosity": SlotDefinition("COMMUNICATION_PREFERENCE", "SINGLE"),
    "output_format": SlotDefinition("COMMUNICATION_PREFERENCE", "SINGLE"),
}


def normalize_identity(
    memory_type: str, slot_code: str, value: str, scope: str = "UNSPECIFIED"
) -> tuple[str, str]:
    """返回范围和成员键；只统一字符格式，不猜测同义、时间或否定关系。

    SET 当前按值的精确摘要区分成员；这是一种保守身份，不能用作语义冲突判断。
    生产中若要屏蔽字典攻击应改为租户分离的 HMAC 并迁移已有键。
    """
    definition = SLOT_REGISTRY.get(slot_code)
    if definition is None or definition.memory_type != memory_type:
        raise MemoryValidationError("Memory 类型和维度不匹配")
    normalized_scope = " ".join(unicodedata.normalize("NFKC", scope).split())
    normalized_value = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized_scope or not normalized_value:
        raise MemoryValidationError("Memory 范围和值不能为空")
    scope_key = (
        "UNSPECIFIED"
        if normalized_scope == "UNSPECIFIED"
        else hashlib.sha256(normalized_scope.encode("utf-8")).hexdigest()
    )
    item_key = (
        "single"
        if definition.cardinality == "SINGLE"
        else hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()
    )
    return scope_key, item_key
