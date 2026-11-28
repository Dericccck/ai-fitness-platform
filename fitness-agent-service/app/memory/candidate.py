"""从当前用户消息受控提取待确认的 Memory 候选。

候选提取本身仍是只读能力：模型可以提出“这句话可能值得记住什么”，但不直接调用
MemoryService，也不把候选当成已生效事实。线上统一由后台异步 ``MemoryExtractionWorker``
调用 ``propose_controlled()`` 加密保存为 PENDING，用户在候选管理接口中批准或拒绝；
批准后才会转为 ACTIVE Memory。对话式显式保存仍走 ``fitness.memory.save.v1`` 与确认链路。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.metrics import HttpMetrics
from app.infrastructure.model_gateway import ModelGateway, ModelResponseError

MemoryCandidateType = Literal[
    "TRAINING_GOAL",
    "TRAINING_PREFERENCE",
    "EQUIPMENT_AVAILABILITY",
    "SCHEDULE_PREFERENCE",
    "COMMUNICATION_PREFERENCE",
]

# 明确的一次性业务/查询动作词：若消息命中这些动作且没有任何长期偏好信号词，
# 在异步 Worker 调 LLM 前直接门控跳过，避免把“星期三帮我约一节坐姿划船的课”误提成长期记忆。
_ONE_OFF_TRANSACTION_MARKERS = (
    "帮我约",
    "约一节",
    "约课",
    "预约",
    "改期",
    "取消预约",
    "查一下",
    "查下",
    "看下",
    "看看我的",
    "营收",
    "完课量",
    "新客",
    "环比",
    "同比",
    "工单",
    "退费",
    "退款",
    "合同",
    "课时余额",
)
_STABLE_PREFERENCE_MARKERS = (
    "记住",
    "请记住",
    "以后",
    "平时",
    "通常",
    "习惯",
    "一般",
    "经常",
    "每次",
    "总是",
    "喜欢",
    "不喜欢",
    "讨厌",
    "偏好",
    "更想",
    "目标",
    "增肌",
    "减脂",
    "减重",
    "塑形",
    "家里有",
    "我有",
    "我只有",
    "只能",
    "有空",
    "也可以",
    "方便",
    "简洁",
    "详细",
    "英文",
    "中文",
    "改成",
    "换成",
    "不再",
)
_FORBIDDEN_TERMS = (
    "诊断",
    "疾病",
    "处方",
    "药物",
    "癌症",
    "怀孕",
    "骨折",
    "心脏病",
    "疼痛",
    "体脂",
    "血压",
    "心率",
    "受伤",
    "手术",
)


def should_attempt_controlled_extraction(user_message: str) -> bool:
    """前置确定性门控：拦截纯一次性交易/查询指令，减少无效异步 LLM 调用与误提取。"""

    text = " ".join(user_message.split())
    if not text:
        return False
    if any(marker in text for marker in _STABLE_PREFERENCE_MARKERS):
        return True
    return not any(marker in text for marker in _ONE_OFF_TRANSACTION_MARKERS)


class MemoryCandidateExtractionError(RuntimeError):
    """候选提取结果无法安全解析或校验。"""


class MemoryCandidateSnapshotEntry(BaseModel):
    """Identity and version of an active memory observed during comparison."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)


class MemoryCandidate(BaseModel):
    """单条待确认候选，只允许映射到当前 Memory 白名单。"""

    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryCandidateType
    memory_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=16)
    # 可选字段兼容历史密文候选；新提取链要求服务端目录和原文证据。
    slot_code: str | None = Field(default=None, max_length=64)
    action: Literal["ADD", "UPDATE", "NOOP", "NEEDS_CLARIFICATION"] = "ADD"
    target_memory_id: str | None = Field(default=None, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    previous_value: str | None = Field(default=None, max_length=500)
    clarification_reason: str | None = Field(default=None, max_length=200)
    evidence_quote: str | None = Field(default=None, max_length=500)
    source_message_id: str | None = Field(default=None, max_length=128)
    write_epoch: int | None = Field(default=None, ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    conflict_snapshot: list[MemoryCandidateSnapshotEntry] | None = Field(
        default=None, max_length=50
    )

    def to_context_line(self) -> str:
        """生成只读候选上下文，不包含模型自由解释或授权结论。"""

        suffix = self.unit or ""
        return f"- {self.memory_type}/{self.memory_key}: {self.value}{suffix}"

    def to_memory_tool_input(self, organization_id: str) -> dict[str, object]:
        """转换成待确认保存工具的参数；调用工具前仍会重新做 Schema 校验。"""

        result: dict[str, object] = {
            "organization_id": organization_id,
            "memory_type": self.memory_type,
            "memory_key": self.memory_key,
            "value": self.value,
        }
        if self.unit:
            result["unit"] = self.unit
        return result


class MemoryCandidateEnvelope(BaseModel):
    """DeepSeek 结构化返回的候选列表，空列表表示当前消息不适合记忆。"""

    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=5)


@dataclass(frozen=True)
class MemoryCandidateRecord:
    """持久化的候选快照；内容已在 Repository 层解密后才进入本对象。

    ``PENDING`` 表示尚未得到用户确认，不能作为已生效的长期事实使用；``APPROVED``
    表示已经晋级为 ACTIVE Memory；``REJECTED`` 和 ``EXPIRED`` 只保留审计结果，不再
    允许继续批准。
    """

    id: str
    subject_user_id: str
    organization_id: str
    candidate: MemoryCandidate
    payload_hash: str
    source_thread_id: str
    source_request_id: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    decision_request_id: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True)
class MemoryCandidateEventRecord:
    """候选生命周期不可变审计事件，不包含候选正文或密文。"""

    id: int
    candidate_id: str
    subject_user_id: str
    organization_id: str
    event_type: Literal["CREATED", "APPROVED", "REJECTED", "EXPIRED", "REDACTED"]
    actor_type: Literal["AGENT", "USER", "SYSTEM"]
    actor_user_id: str | None
    status_after: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    request_id: str
    decision_request_id: str | None
    payload_hash: str
    created_at: datetime


class _ControlledCandidateItem(BaseModel):
    """受控提取模型返回的单条结构，包含稳定性分类字段，不污染持久化密文 Schema。"""

    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryCandidateType
    memory_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    slot_code: str | None = Field(default=None, max_length=64)
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=16)
    evidence_quote: str | None = Field(default=None, max_length=500)
    stability: Literal["STABLE_PREFERENCE", "ONE_OFF_REQUEST"] = "STABLE_PREFERENCE"


class _ControlledCandidateEnvelope(BaseModel):
    """受控提取模型返回的候选包装，空列表表示当前消息不含长期偏好。"""

    model_config = ConfigDict(extra="forbid")

    candidates: list[_ControlledCandidateItem] = Field(default_factory=list, max_length=5)


class MemoryCandidateExtractionService:
    """使用 DeepSeek 做受控候选提取，将模型输出严格限制在只读白名单与稳定性边界内。"""

    def __init__(self, models: ModelGateway, metrics: HttpMetrics | None = None) -> None:
        self.models = models
        self.metrics = metrics

    async def propose_controlled(self, user_message: str) -> tuple[MemoryCandidate, ...]:
        """受控目录 + 前置门控 + 稳定性分类 + 逐字证据校验；空结果与提取错误分开。"""
        from .registry import SLOT_REGISTRY

        if not should_attempt_controlled_extraction(user_message):
            if self.metrics is not None:
                self.metrics.record_memory_candidate_event("extraction_skipped")
            return ()

        slots = {name: definition.memory_type for name, definition in SLOT_REGISTRY.items()}
        messages = [
            {
                "role": "system",
                "content": (
                    "仅从用户原话中提取明确、稳定、跨会话有价值的低敏个人健身偏好；"
                    "一次性安排或临时交易（如“星期三帮我约一节坐姿划船的课”“帮我查明天的课”）、"
                    "健康诊断、疼痛、预约、合同、支付、模型推断均不得提取。\n"
                    '仅返回JSON对象：{"candidates":[{"memory_type":"类型",'
                    '"memory_key":"槽位","slot_code":"槽位","value":"原意","unit":null,'
                    '"evidence_quote":"逐字原文","stability":"STABLE_PREFERENCE|ONE_OFF_REQUEST"}]}。\n'
                    "规则：只有明确长期偏好才标记 STABLE_PREFERENCE；一次性预约/查询标记 ONE_OFF_REQUEST 或不输出；"
                    "没有明确长期表达时返回空数组；每条 evidence_quote 必须逐字来自用户原文。\n"
                    "以下是唯一允许的槽位到类型映射：" + json.dumps(slots, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": user_message},
        ]
        try:
            raw = await self.models.chat_json(messages)
            envelope = _ControlledCandidateEnvelope.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, ModelResponseError) as exc:
            if self.metrics is not None:
                self.metrics.record_memory_candidate_event("extraction_failed")
            raise MemoryCandidateExtractionError("Memory 候选未通过结构化校验") from exc
        safe: list[MemoryCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for item in envelope.candidates:
            slot = item.slot_code
            quote = item.evidence_quote
            if (
                item.stability != "STABLE_PREFERENCE"
                or not slot
                or slots.get(slot) != item.memory_type
                or not quote
                or quote not in user_message
                or item.memory_key != slot
                or any(term in item.value or term in quote for term in _FORBIDDEN_TERMS)
                or any(ord(char) < 32 for char in item.value)
            ):
                continue
            cleaned_value = " ".join(item.value.split())
            cleaned_unit = " ".join(item.unit.split()) if item.unit else None
            if cleaned_unit is not None and any(ord(char) < 32 for char in cleaned_unit):
                continue
            identity = (item.memory_type, slot, cleaned_value)
            if not cleaned_value or identity in seen:
                continue
            seen.add(identity)
            # 模型不能决定更新目标、内部版本或写入 epoch。
            safe.append(
                MemoryCandidate(
                    memory_type=item.memory_type,
                    memory_key=slot,
                    slot_code=slot,
                    value=cleaned_value,
                    unit=cleaned_unit,
                    evidence_quote=quote,
                    action="ADD",
                )
            )
        if self.metrics is not None:
            self.metrics.record_memory_candidate_event("extraction_succeeded")
            self.metrics.record_memory_candidate_event(
                "extraction_filtered", len(envelope.candidates) - len(safe)
            )
        return tuple(safe)
