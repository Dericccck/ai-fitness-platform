"""Memory 候选的提取、持久化和用户决定服务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from app.confirmation.cipher import ConfirmationPayloadCipherError
from app.infrastructure.agent_context import AgentIdentity

from .candidate import (
    MemoryCandidate,
    MemoryCandidateExtractionService,
    MemoryCandidateRecord,
)
from .candidate_repository import (
    MemoryCandidateRepository,
    MemoryCandidateStateError,
)
from .models import FitnessMemory, validate_memory_owner
from .service import MemoryService


class MemoryCandidatePersistenceError(RuntimeError):
    """候选无法持久化或恢复。"""

    def __init__(self, message: str, candidates: tuple[MemoryCandidate, ...] = ()) -> None:
        super().__init__(message)
        # 即使候选落库暂时失败，也可以把“未确认”提示交给当前对话，避免普通问答被
        # 候选辅助链路阻断；调用方必须把这些候选当作临时上下文，不能声称已经保存。
        self.candidates = candidates


class MemoryCandidateDecisionResult:
    """候选决定后的稳定结果；批准时包含新建或幂等复用的 ACTIVE Memory。"""

    def __init__(
        self,
        candidate: MemoryCandidateRecord,
        memory: FitnessMemory | None = None,
    ) -> None:
        self.candidate = candidate
        self.memory = memory


class MemoryCandidateService:
    """把候选提取、加密持久化和 Memory 晋级串成一个受控边界。"""

    def __init__(
        self,
        extractor: MemoryCandidateExtractionService,
        repository: MemoryCandidateRepository,
        memory_service: MemoryService,
        *,
        ttl_hours: int = 24,
    ) -> None:
        if ttl_hours < 1 or ttl_hours > 168:
            raise ValueError("candidate ttl must be between 1 and 168 hours")
        self.extractor = extractor
        self.repository = repository
        self.memory_service = memory_service
        self.ttl = timedelta(hours=ttl_hours)

    async def propose(
        self,
        *,
        user_message: str,
        identity: AgentIdentity,
        thread_id: str,
        source_request_id: str | None,
    ) -> tuple[MemoryCandidate, ...]:
        """提取候选并在单机构范围内加密持久化；返回值仍标记为未确认。"""

        candidates = await self.extractor.propose(user_message)
        if not candidates or len(identity.organization_ids) != 1 or not source_request_id:
            # 多机构时不能擅自选择归属；候选仍可交给主模型询问用户，但不落库。
            return candidates
        organization_id = next(iter(identity.organization_ids))
        expires_at = datetime.now(UTC) + self.ttl
        for candidate in candidates:
            try:
                await self.repository.create_pending(
                    identity=identity,
                    organization_id=organization_id,
                    candidate=candidate,
                    source_thread_id=thread_id,
                    source_request_id=source_request_id,
                    expires_at=expires_at,
                )
            except (MemoryCandidateStateError, ConfirmationPayloadCipherError) as exc:
                raise MemoryCandidatePersistenceError("Memory 候选持久化失败", candidates) from exc
        return candidates

    async def list_pending(
        self, *, identity: AgentIdentity, organization_id: str, limit: int = 50
    ) -> list[MemoryCandidateRecord]:
        """查询本人、指定机构、仍在确认期限内的候选。"""

        if limit < 1 or limit > 100:
            raise ValueError("candidate list limit must be between 1 and 100")
        validate_memory_owner(identity, organization_id)
        return await self.repository.list_pending(
            identity=identity, organization_id=organization_id, limit=limit
        )

    async def decide(
        self,
        candidate_id: str,
        *,
        identity: AgentIdentity,
        decision: Literal["APPROVE", "REJECT"],
        decision_request_id: str,
    ) -> MemoryCandidateDecisionResult:
        """批准候选时先幂等保存 ACTIVE Memory，再记录候选已批准。

        两步操作之间如果进程崩溃，候选可能暂时仍为 PENDING；相同决定请求重试时，
        MemoryService 会按稳定 source_request_id 幂等收敛，最终再把候选改为 APPROVED。
        这样不会出现“候选已批准但 Memory 尚未保存”的假成功状态。
        """

        if not decision_request_id.strip():
            raise ValueError("decision_request_id is required")
        candidate = await self.repository.get_for_subject(candidate_id, identity=identity)
        if candidate.expires_at <= datetime.now(UTC) and candidate.status == "PENDING":
            raise MemoryCandidateStateError("memory candidate has expired")
        if decision == "REJECT":
            rejected = await self.repository.decide(
                candidate_id,
                identity=identity,
                decision="REJECTED",
                decision_request_id=decision_request_id,
                now=datetime.now(UTC),
            )
            return MemoryCandidateDecisionResult(rejected)
        if candidate.status == "APPROVED" and candidate.decision_request_id == decision_request_id:
            memory = await self._save_candidate_memory(candidate, identity)
            return MemoryCandidateDecisionResult(candidate, memory)
        if candidate.status != "PENDING":
            raise MemoryCandidateStateError("memory candidate decision is already final")
        memory = await self._save_candidate_memory(candidate, identity)
        approved = await self.repository.decide(
            candidate_id,
            identity=identity,
            decision="APPROVED",
            decision_request_id=decision_request_id,
            now=datetime.now(UTC),
        )
        return MemoryCandidateDecisionResult(approved, memory)

    async def _save_candidate_memory(
        self, candidate: MemoryCandidateRecord, identity: AgentIdentity
    ) -> FitnessMemory:
        return await self.memory_service.save(
            # 这里必须沿用本次请求重新验证过的签名身份，不能伪造一个“永久有效”的
            # 内部身份。候选仓储已经按 subject/org 做了一次隔离，MemoryService 再做
            # 一次 owner 校验，形成纵深防御。
            identity=identity,
            organization_id=candidate.organization_id,
            memory_type=candidate.candidate.memory_type,
            memory_key=candidate.candidate.memory_key,
            value=candidate.candidate.value,
            unit=candidate.candidate.unit,
            expires_at=None,
            # 候选 ID 是一次批准事实的稳定幂等键；这样进程在“Memory 已写入、候选
            # 状态尚未更新”之间崩溃时，重试不会把 Memory 版本重复加一。
            source_request_id=f"memory-candidate:{candidate.id}:approve",
        )
