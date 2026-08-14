"""健身 Memory 的业务校验和生命周期服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from app.infrastructure.agent_context import AgentIdentity

from .models import FitnessMemory, MemoryType, MemoryValidationError, validate_memory_owner
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

    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository

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
    ) -> FitnessMemory:
        """保存用户明确确认的结构化偏好；同一键再次保存表示纠正旧记忆。"""

        validate_memory_owner(identity, organization_id)
        normalized_type = memory_type.strip().upper()
        if normalized_type not in _MEMORY_TYPES:
            raise MemoryValidationError("memory type is outside the current fitness scope")
        normalized_key = _validate_text(memory_key, "memory_key", 64)
        normalized_value = _validate_text(value, "value", 500)
        if any(term in normalized_value for term in _FORBIDDEN_TERMS):
            raise MemoryValidationError(
                "health diagnosis, disease, medication, and treatment facts are not stored as v1 memory"
            )
        normalized_unit = _validate_text(unit, "unit", 16) if unit is not None else None
        if expires_at is not None:
            expires_at = _as_utc(expires_at)
            if expires_at <= datetime.now(UTC):
                raise MemoryValidationError("memory expiry must be in the future")
        if not source_request_id.strip():
            raise MemoryValidationError("source_request_id is required for idempotent writes")
        content: dict[str, Any] = {"key": normalized_key, "value": normalized_value}
        if normalized_unit:
            content["unit"] = normalized_unit
        return await self.repository.save(
            identity=identity,
            organization_id=organization_id,
            memory_type=cast(MemoryType, normalized_type),
            memory_key=normalized_key,
            content=content,
            expires_at=expires_at,
            source_request_id=source_request_id,
        )

    async def list_active(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> list[FitnessMemory]:
        """返回当前用户在指定机构内可用于计划生成的 Memory。"""

        validate_memory_owner(identity, organization_id)
        return await self.repository.list_active(identity=identity, organization_id=organization_id)

    async def revoke(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        memory_id: str,
        expected_version: int,
    ) -> FitnessMemory:
        """撤销本人 Memory；撤销后不再进入 Prompt，但保留审计事实。"""

        validate_memory_owner(identity, organization_id)
        if not memory_id.strip() or expected_version < 1:
            raise MemoryValidationError("memory id and positive expected version are required")
        return await self.repository.revoke(
            identity=identity,
            organization_id=organization_id,
            memory_id=memory_id,
            expected_version=expected_version,
        )

    async def expire_due(self, *, limit: int = 500) -> int:
        """执行一批到期标记，后续由独立 Worker/定时任务触发。"""

        if limit < 1 or limit > 5000:
            raise MemoryValidationError("expiry batch limit must be between 1 and 5000")
        return await self.repository.expire_due(limit=limit)


def _validate_text(value: str | None, field_name: str, max_length: int) -> str:
    if value is None:
        raise MemoryValidationError(f"{field_name} is required")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > max_length:
        raise MemoryValidationError(f"{field_name} is blank or too long")
    return normalized


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
