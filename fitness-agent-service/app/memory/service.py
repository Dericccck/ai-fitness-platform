"""健身 Memory 的业务校验和生命周期服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from app.infrastructure.agent_context import AgentIdentity

from .models import (
    FitnessMemory,
    MemoryEventRecord,
    MemoryType,
    MemoryValidationError,
    validate_memory_owner,
)
from .repository import MemoryRepository

_MEMORY_TYPES: frozenset[str] = frozenset(
    {
        "TRAINING_GOAL",
        "TRAINING_PREFERENCE",
        "EQUIPMENT_AVAILABILITY",
        "SCHEDULE_PREFERENCE",
        "COMMUNICATION_PREFERENCE",
    }
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


class MemoryService:
    """只处理用户明确提供的低敏、可撤销长期偏好。"""

    def __init__(
        self,
        repository: MemoryRepository,
        summary_repository: Any | None = None,
        checkpoint_cleaner: Any | None = None,
    ) -> None:
        self.repository = repository
        self.summary_repository = summary_repository
        self.checkpoint_cleaner = checkpoint_cleaner

    async def save(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_type: str,
        memory_key: str,
        value: str,
        unit: str | None,
        expires_at: datetime | None,
        source_request_id: str,
        request_id: str | None = None,
    ) -> FitnessMemory:
        """保存用户明确确认的结构化偏好；同一键再次保存表示纠正旧记忆。"""

        validate_memory_owner(identity, organization_id)
        normalized_type, normalized_key, content, expires_at = _normalize_memory_payload(
            memory_type=memory_type,
            memory_key=memory_key,
            value=value,
            unit=unit,
            expires_at=expires_at,
        )
        if not source_request_id.strip():
            raise MemoryValidationError("幂等写入需要 source_request_id")
        return await self.repository.save(
            identity=identity,
            organization_id=organization_id,
            memory_type=normalized_type,
            memory_key=normalized_key,
            content=content,
            expires_at=expires_at,
            source_request_id=source_request_id,
            request_id=request_id,
        )

    async def correct(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_id: str,
        expected_version: int,
        value: str,
        unit: str | None,
        expires_at: datetime | None,
        source_request_id: str,
        request_id: str | None = None,
    ) -> FitnessMemory:
        """按 Memory ID 和期望版本纠正本人 Memory。

        纠正不会改变原 Memory 的类型和稳定键，只替换用户明确修改的值、单位和过期策略。
        这样管理页面的旧版本不会覆盖其他请求刚刚保存的新版本；成功后仍写入 ``SAVED``
        审计事件，表示这是一条新的已确认事实。
        """

        validate_memory_owner(identity, organization_id)
        if not memory_id.strip() or expected_version < 1 or not source_request_id.strip():
            raise MemoryValidationError(
                "必须提供 memory id、正数 expected_version 和 source_request_id"
            )
        target = await self.repository.get_for_subject(memory_id, identity=identity)
        if target.organization_id != organization_id:
            raise MemoryValidationError("Memory 所属机构不在已签名身份范围内")
        _, _, content, normalized_expiry = _normalize_memory_payload(
            # 类型和稳定键从数据库目标读取，管理页面只能修改值、单位和过期策略，不能
            # 借纠正接口改变 Memory 的身份分类或把它迁移到另一条稳定键。
            memory_type=target.memory_type,
            memory_key=target.memory_key,
            value=value,
            unit=unit,
            expires_at=expires_at,
        )
        result = await self.repository.correct(
            identity=identity,
            organization_id=organization_id,
            memory_id=memory_id,
            expected_version=expected_version,
            content=content,
            expires_at=normalized_expiry,
            source_request_id=source_request_id,
            request_id=request_id,
        )
        await self._invalidate_derived_context(identity.subject)
        return result

    async def list_active(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> list[FitnessMemory]:
        """返回当前用户在指定机构内可用于计划生成的 Memory。"""

        validate_memory_owner(identity, organization_id)
        return await self.repository.list_active(identity=identity, organization_id=organization_id)

    async def get_for_subject(self, *, identity: AgentIdentity, memory_id: str) -> FitnessMemory:
        """读取本人 Memory 的完整领域对象，机构由数据库主体范围决定。"""

        if not memory_id.strip():
            raise MemoryValidationError("必须提供 memory id")
        return await self.repository.get_for_subject(memory_id, identity=identity)

    async def revoke(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_id: str,
        expected_version: int,
        source_request_id: str,
        request_id: str | None = None,
    ) -> FitnessMemory:
        """撤销本人 Memory；撤销后不再进入 Prompt，但保留审计事实。"""

        validate_memory_owner(identity, organization_id)
        if not memory_id.strip() or expected_version < 1 or not source_request_id.strip():
            raise MemoryValidationError(
                "必须提供 memory id、正数 expected_version 和 source_request_id"
            )
        result = await self.repository.revoke(
            identity=identity,
            organization_id=organization_id,
            memory_id=memory_id,
            expected_version=expected_version,
            source_request_id=source_request_id,
            request_id=request_id,
        )
        await self._invalidate_derived_context(identity.subject)
        return result

    async def _invalidate_derived_context(self, subject_user_id: str) -> None:
        if self.summary_repository is None:
            return
        try:
            thread_ids = await self.summary_repository.list_thread_ids_for_subject(subject_user_id)
            await self.summary_repository.invalidate_for_subject(subject_user_id)
            if self.checkpoint_cleaner is not None and thread_ids:
                await self.checkpoint_cleaner.delete_threads(thread_ids)
        except Exception:
            # Memory 已经完成撤销；摘要清理失败必须可观测，但不能让用户重试造成
            # 版本冲突。后续请求也会在摘要读取失败时安全降级为不使用摘要。
            import structlog

            structlog.get_logger("memory.lifecycle").exception(
                "derived_context_invalidation_failed", subject_user_id=subject_user_id
            )

    async def list_events(
        self, *, identity: AgentIdentity, memory_id: str, limit: int = 50
    ) -> list[MemoryEventRecord]:
        """返回本人 Memory 的生命周期摘要，并对不存在/越权统一按不存在处理。"""

        if not memory_id.strip():
            raise MemoryValidationError("必须提供 memory id")
        # 先读主体范围内的 Memory，避免越权 ID 因“没有事件”泄露存在性差异。
        memory = await self.repository.get_for_subject(memory_id, identity=identity)
        validate_memory_owner(identity, memory.organization_id)
        return await self.repository.list_events(memory_id, identity=identity, limit=limit)

    async def expire_due(self, *, limit: int = 500) -> int:
        """执行一批到期标记，后续由独立 Worker/定时任务触发。"""

        if limit < 1 or limit > 5000:
            raise MemoryValidationError("过期批次限制必须在 1 到 5000 之间")
        return await self.repository.expire_due(limit=limit)


def _validate_text(value: str | None, field_name: str, max_length: int) -> str:
    if value is None:
        raise MemoryValidationError(f"必须提供 {field_name}")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > max_length:
        raise MemoryValidationError(f"{field_name} 不能为空白且长度不能过长")
    return normalized


def _normalize_memory_payload(
    *,
    memory_type: str,
    memory_key: str,
    value: str,
    unit: str | None,
    expires_at: datetime | None,
) -> tuple[MemoryType, str, dict[str, Any], datetime | None]:
    """统一保存和纠正的低敏字段校验，避免两个写入口出现安全规则漂移。"""

    normalized_type = memory_type.strip().upper()
    if normalized_type not in _MEMORY_TYPES:
        raise MemoryValidationError("Memory 类型不在当前健身业务范围内")
    normalized_key = _validate_text(memory_key, "memory_key", 64)
    normalized_value = _validate_text(value, "value", 500)
    if any(term in normalized_value for term in _FORBIDDEN_TERMS):
        raise MemoryValidationError("v1 Memory 不保存健康诊断、疾病、药物和治疗事实")
    normalized_unit = _validate_text(unit, "unit", 16) if unit is not None else None
    normalized_expiry = _as_utc(expires_at) if expires_at is not None else None
    if normalized_expiry is not None and normalized_expiry <= datetime.now(UTC):
        raise MemoryValidationError("Memory 过期时间必须在未来")
    content: dict[str, Any] = {"key": normalized_key, "value": normalized_value}
    if normalized_unit:
        content["unit"] = normalized_unit
    return cast(MemoryType, normalized_type), normalized_key, content, normalized_expiry


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
