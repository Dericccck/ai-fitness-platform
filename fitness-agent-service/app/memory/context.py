"""按授权主体和用途选择确认过的记忆；不执行对外授权或持久化。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.memory.models import FitnessMemory, MemoryValidationError

MemoryPurpose = Literal[
    "training_plan", "conversation", "fitness_qa", "booking", "document", "support"
]
_TRAINING_TYPES = frozenset(
    {"TRAINING_GOAL", "TRAINING_PREFERENCE", "EQUIPMENT_AVAILABILITY", "SCHEDULE_PREFERENCE"}
)
_PURPOSE_TYPES = {
    "training_plan": _TRAINING_TYPES | {"COMMUNICATION_PREFERENCE"},
    "conversation": frozenset({"COMMUNICATION_PREFERENCE"}),
    "fitness_qa": frozenset({"COMMUNICATION_PREFERENCE"}),
    "booking": frozenset({"COMMUNICATION_PREFERENCE", "SCHEDULE_PREFERENCE"}),
    "document": frozenset({"COMMUNICATION_PREFERENCE"}),
    "support": frozenset({"COMMUNICATION_PREFERENCE"}),
}


@dataclass(frozen=True)
class MemoryContextPacket:
    """正文仅运行时使用；引用清单表示注入而非模型实际采用。"""

    memories: tuple[FitnessMemory, ...]
    purpose: MemoryPurpose
    # 仅作为提示和追踪信息，不包含正文；未知适用范围不能默认为全局事实。
    uncertain_scope_ids: tuple[str, ...] = ()
    excluded_scope_ids: tuple[str, ...] = ()
    excluded_budget_ids: tuple[str, ...] = ()

    def prompt_text(self) -> str:
        return "\n".join(memory.to_prompt_line() for memory in self.memories)

    def references(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {"id": memory.id, "version": memory.version, "source_type": memory.source_type}
            for memory in self.memories
        )


class MemoryContextBuilder:
    """调用方先完成 Gateway 授权；本类独立检查结果的主体、机构和有效期。

    非训练场景仅自动读取沟通偏好；由可信上层明确提供相关类别后才可扩展，不能
    把模型生成的类别视作授权。代读只允许训练计划并且不得读取学员沟通偏好。
    """

    def build(
        self,
        memories: Iterable[FitnessMemory],
        *,
        actor_id: str,
        subject_id: str,
        organization_id: str,
        purpose: MemoryPurpose,
        delegated_authorized: bool = False,
        relevant_types: frozenset[str] = frozenset(),
        relevant_memory_ids: frozenset[str] | None = None,
        relevant_slots: frozenset[str] | None = None,
        max_characters: int = 6000,
        now: datetime | None = None,
        applicable_scopes: frozenset[str] = frozenset(),
    ) -> MemoryContextPacket:
        if (
            not actor_id
            or not subject_id
            or not organization_id
            or purpose not in _PURPOSE_TYPES
            or max_characters < 1
        ):
            raise MemoryValidationError("无效的 Memory 上下文范围")
        delegated = actor_id != subject_id
        if delegated and (not delegated_authorized or purpose != "training_plan"):
            raise MemoryValidationError("未授权的 Memory 代读")
        if not relevant_types <= _TRAINING_TYPES:
            raise MemoryValidationError("未知或不允许的相关 Memory 类别")
        allowed = _PURPOSE_TYPES[purpose] | relevant_types
        if delegated:
            allowed = allowed & _TRAINING_TYPES
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise MemoryValidationError("Memory 读取时间必须包含时区")
        selected: list[FitnessMemory] = []
        uncertain: list[str] = []
        excluded_scope: list[str] = []
        for memory in memories:
            # 不信任仓储或上游的范围过滤；一旦范围错误，拒绝整批，而不是悄悄隐藏越权问题。
            if memory.subject_user_id != subject_id or memory.organization_id != organization_id:
                raise MemoryValidationError("Memory 返回范围与授权主体不一致")
            if memory.memory_type not in allowed:
                continue
            if memory.expires_at is not None and memory.expires_at.tzinfo is None:
                continue
            if not memory.is_active(instant):
                continue
            # 对非训练场景自动加入沟通偏好，额外训练记忆可被可信上游按 ID 和维度收窄。
            if purpose != "training_plan" and memory.memory_type != "COMMUNICATION_PREFERENCE":
                if relevant_memory_ids is not None and memory.id not in relevant_memory_ids:
                    continue
                slot = getattr(memory, "slot_code", None) or memory.memory_key
                if relevant_slots is not None and slot not in relevant_slots:
                    continue
            scope = getattr(memory, "scope_key", None)
            if scope not in (None, "", "UNSPECIFIED", "GLOBAL") and scope not in applicable_scopes:
                excluded_scope.append(memory.id)
                continue
            if scope in (None, "", "UNSPECIFIED"):
                uncertain.append(memory.id)
            selected.append(memory)
        # 固定字符预算只是粗略限制，不等价于模型 token 计数。训练计划不可静默截断
        # 必要条件；普通对话优先保留沟通偏好，其他项按上游顺序保留。
        total = sum(len(item.to_prompt_line()) + 1 for item in selected)
        omitted: list[str] = []
        if total > max_characters:
            if purpose == "training_plan":
                raise MemoryValidationError("训练记忆超出上下文预算，需要缩小范围或询问")
            ranked = sorted(
                selected, key=lambda item: item.memory_type != "COMMUNICATION_PREFERENCE"
            )
            kept: list[FitnessMemory] = []
            remaining = max_characters
            for item in ranked:
                cost = len(item.to_prompt_line()) + 1
                if cost <= remaining:
                    kept.append(item)
                    remaining -= cost
                else:
                    omitted.append(item.id)
            selected = kept
        chosen = {item.id for item in selected}
        return MemoryContextPacket(
            tuple(selected),
            purpose,
            uncertain_scope_ids=tuple(id for id in uncertain if id in chosen),
            excluded_scope_ids=tuple(excluded_scope),
            excluded_budget_ids=tuple(omitted),
        )
