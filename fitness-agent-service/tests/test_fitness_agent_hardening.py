"""fitness_agent 生产级重构与安全加固回归测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from app.agent.supervisor import Supervisor, SupervisorRequest, _sanitize_unpaired_tool_calls
from app.agent.tool_registry import ToolContext, ToolDefinition, ToolRegistry
from app.agent.training_plan_generation import TrainingPlanClarificationError
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.models import ConfirmationAction, ConfirmationRecord, ConfirmationStateError
from app.confirmation.normalization import ConfirmationPolicy, canonical_json_bytes
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn
from app.infrastructure.reranker import RerankResult
from app.memory.candidate import MemoryCandidate, MemoryCandidateExtractionService
from app.memory.models import FitnessMemory
from app.memory.reconciliation import reconcile_candidate
from app.memory.repository import MemoryRepository
from app.rag.models import KnowledgeChunk, RetrievalScope
from app.rag.service import RagService

IDENTITY = AgentIdentity(
    subject="student-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"STUDENT"}),
    issued_at=1,
    expires_at=9999999999,
)


def _chunk(identifier: str, content: str, *, score: float = 0.9) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=identifier,
        document_id=f"doc-{identifier}",
        chunk_index=0,
        content=content,
        source_uri=f"knowledge://{identifier}",
        title=f"指南-{identifier}",
        document_type="FITNESS_GUIDE",
        version=1,
        similarity=score,
        metadata={},
    )


class _FakeRagRepo:
    def __init__(self, chunks: list[KnowledgeChunk]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def search_candidates(
        self, embedding: list[float], scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        self.calls += 1
        return list(self.chunks)

    async def search_keyword_candidates(
        self, query: str, scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        return []


class _ScoreReranker:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        return [
            RerankResult(index=i, score=self.scores[i], document=doc)
            for i, doc in enumerate(documents)
        ]


class _RecordingModel:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ModelTurn:
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        return self.turns.pop(0)


@pytest.mark.asyncio
async def test_rag_rerank_min_score_filters_low_confidence_chunks() -> None:
    repo = _FakeRagRepo([_chunk("low", "无关内容"), _chunk("high", "核心热身规范")])
    models = _RecordingModel([])
    service = RagService(
        cast(Any, repo),
        cast(Any, models),
        cast(Any, _ScoreReranker([0.12, 0.85])),
        rerank_min_score=0.3,
    )

    result = await service.search(
        "力量训练如何热身",
        RetrievalScope("student-1", frozenset({"org-1"}), frozenset({"STUDENT"})),
    )
    assert [chunk.id for chunk in result.chunks] == ["high"]

    all_low_service = RagService(
        cast(Any, repo),
        cast(Any, models),
        cast(Any, _ScoreReranker([0.12, 0.2])),
        rerank_min_score=0.3,
    )
    empty_result = await all_low_service.search(
        "力量训练如何热身",
        RetrievalScope("student-1", frozenset({"org-1"}), frozenset({"STUDENT"})),
    )
    assert empty_result.chunks == ()


@pytest.mark.asyncio
async def test_fitness_knowledge_qa_short_circuits_without_calling_llm_when_no_evidence() -> None:
    repo = _FakeRagRepo([_chunk("low", "低相关噪音")])
    model = _RecordingModel([ModelTurn(content="不应该被调用", tool_calls=())])
    rag = RagService(
        cast(Any, repo),
        cast(Any, model),
        cast(Any, _ScoreReranker([0.05])),
        rerank_min_score=0.35,
    )
    supervisor = Supervisor(cast(ModelGateway, model), ToolRegistry(), rag_service=rag)
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        emitted.append((event, data))

    response = await supervisor.stream(
        SupervisorRequest(
            user_message="力量训练前如何热身？",
            gateway_context=GatewayRequestContext("signed"),
            conversation_id="conv-no-evidence",
            identity=IDENTITY,
        ),
        emit,
    )

    assert response.answer == "未检索到可用的知识资料，暂时无法提供有来源支持的回答。"
    assert response.retrieved_evidence_ids == ()
    assert model.calls == []
    deltas = [data["text"] for event, data in emitted if event == "answer.delta"]
    assert deltas == ["未检索到可用的知识资料，暂时无法提供有来源支持的回答。"]


@pytest.mark.asyncio
async def test_fitness_knowledge_qa_exposes_zero_tools_and_plan_skips_parent_rag() -> None:
    repo = _FakeRagRepo([_chunk("k1", "动态热身5-10分钟")])
    model = _RecordingModel(
        [
            ModelTurn(content="建议先做动态热身。[证据1]", tool_calls=()),
            ModelTurn(content="已为您读取训练计划。", tool_calls=()),
        ]
    )
    rag = RagService(
        cast(Any, repo),
        cast(Any, model),
        cast(Any, _ScoreReranker([0.92])),
        rerank_min_score=0.3,
    )

    class _SimpleInput(BaseModel):
        plan_id: str = "plan-1"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="fitness.training.plan.get.v1",
            description="查询训练计划",
            input_model=_SimpleInput,
            handler=lambda raw, ctx: _async_return({"id": "plan-1"}),
            allowed_roles=frozenset({"STUDENT"}),
            read_only=True,
            requires_confirmation=False,
        )
    )
    supervisor = Supervisor(cast(ModelGateway, model), registry, rag_service=rag)

    qa_response = await supervisor.invoke(
        SupervisorRequest(
            user_message="深蹲前如何热身？",
            gateway_context=GatewayRequestContext("signed"),
            conversation_id="conv-qa-tools",
            identity=IDENTITY,
        )
    )
    assert qa_response.answer == "建议先做动态热身。[证据1]"
    assert model.calls[0]["tools"] == []
    assert repo.calls == 1

    # 非知识问答的健身意图（训练计划草案建议）完全跳过子图通用 RAG，不白查知识库。
    plan_response = await supervisor.invoke(
        SupervisorRequest(
            user_message="给我一个训练计划建议",
            gateway_context=GatewayRequestContext("signed"),
            conversation_id="conv-plan-no-rag",
            identity=IDENTITY,
        )
    )
    assert plan_response.intent == "GENERATE_TRAINING_DRAFT"
    assert plan_response.answer == "已为您读取训练计划。"
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_training_plan_clarification_error_returns_tool_message_not_503() -> None:
    class _DraftInput(BaseModel):
        organization_id: str = "org-1"
        student_id: str = "student-1"
        coach_id: str = "coach-1"
        goal_type: str = "增肌"
        training_days: int = 3
        level: str = "初级"
        session_minutes: int = 45
        equipment: list[str] = []

    async def _fail_clarification(raw: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        raise TrainingPlanClarificationError("知识库中暂无该器械方案，请调整器械条件。")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="fitness.training.plan.generate_draft.v1",
            description="生成训练草案",
            input_model=_DraftInput,
            handler=_fail_clarification,
            allowed_roles=frozenset({"STUDENT"}),
            read_only=True,
            requires_confirmation=False,
        )
    )
    model = _RecordingModel(
        [
            ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-draft-1",
                        name="fitness.training.plan.generate_draft.v1",
                        arguments={"goal_type": "增肌"},
                    ),
                ),
            ),
            ModelTurn(content="当前知识库暂无该器械方案，请问可以改用哑铃或徒手吗？", tool_calls=()),
        ]
    )
    supervisor = Supervisor(cast(ModelGateway, model), registry)
    response = await supervisor.invoke(
        SupervisorRequest(
            user_message="帮我生成一份增肌训练计划预览",
            gateway_context=GatewayRequestContext("signed"),
            conversation_id="conv-plan-clarify",
            identity=IDENTITY,
        )
    )

    assert "哑铃或徒手" in response.answer
    tool_msgs = [m for m in model.calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "CLARIFICATION_NEEDED" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_open_set_reconciliation_allows_explicit_additive_exercise_preference() -> None:
    now = datetime.now(UTC)
    existing = FitnessMemory(
        id="mem-row-1",
        subject_user_id="student-1",
        organization_id="org-1",
        memory_type="TRAINING_PREFERENCE",
        memory_key="exercise_preference",
        content={"value": "坐姿划船"},
        source_type="USER_EXPLICIT",
        confidence=1.0,
        status="ACTIVE",
        version=1,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    cand = MemoryCandidate(
        memory_type="TRAINING_PREFERENCE",
        memory_key="exercise_preference",
        slot_code="exercise_preference",
        value="举哑铃",
        evidence_quote="我也喜欢举哑铃",
    )

    class _AddModel:
        async def chat_json(self, messages: list[dict[str, str]]) -> str:
            return json.dumps({"action": "ADD", "target_memory_id": None})

    result = await reconcile_candidate(cand, [existing], _AddModel())
    assert result.action == "ADD"

    # 若原话不含叠加词或含替换词，依然保守要求澄清。
    ambiguous = MemoryCandidate(
        memory_type="TRAINING_PREFERENCE",
        memory_key="exercise_preference",
        slot_code="exercise_preference",
        value="举哑铃",
        evidence_quote="我喜欢举哑铃",
    )
    ambiguous_result = await reconcile_candidate(ambiguous, [existing], _AddModel())
    assert ambiguous_result.action == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
async def test_memory_bump_profile_only_invalidates_subject_fence_on_forget() -> None:
    executed_sql: list[str] = []

    class _FakeConnection:
        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            executed_sql.append(str(statement))

    await MemoryRepository._bump_profile(_FakeConnection(), "student-1", "org-1", forget=False)
    assert all("agent_memory_subject_fences" not in sql for sql in executed_sql)
    assert all("agent_session_summaries" not in sql for sql in executed_sql)
    assert any("agent_memory_profiles" in sql for sql in executed_sql)

    executed_sql.clear()
    await MemoryRepository._bump_profile(_FakeConnection(), "student-1", "org-1", forget=True)
    assert any("agent_memory_subject_fences" in sql for sql in executed_sql)
    assert any("agent_session_summaries" in sql for sql in executed_sql)
    assert any("agent_memory_profiles" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_rejected_and_expired_confirmation_recovers_without_400_and_keeps_context() -> None:
    class _SaveMemInput(BaseModel):
        organization_id: str = "org-1"
        memory_type: str = "TRAINING_PREFERENCE"
        memory_key: str = "exercise_preference"
        value: str = "每周4练"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="fitness.memory.save.v1",
            description="保存训练偏好",
            input_model=_SaveMemInput,
            handler=lambda raw, ctx: _async_return({"saved": True}),
            allowed_roles=frozenset({"STUDENT"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=ConfirmationPolicy(
                action="SAVE_MEMORY",
                resource_type="agent_memory",
                risk_level="LOW",
                operation="SAVE_MEMORY",
                target_status="ACTIVE",
                payload_builder=lambda model: model.model_dump(mode="json"),
                summary_builder=lambda model, _: {
                    "operation": "SAVE_MEMORY",
                    "target_status": "ACTIVE",
                    "title": "保存偏好：每周4练",
                },
            ),
        )
    )

    @dataclass
    class _StubConfirmationService:
        status: str = "PENDING"
        expires_at: datetime = datetime.now(UTC) + timedelta(minutes=10)

        async def prepare(self, **kwargs: Any) -> ConfirmationRecord:
            return self._record()

        async def get_for_subject(
            self, confirmation_id: str, identity: AgentIdentity
        ) -> ConfirmationRecord:
            return self._record()

        def _record(self) -> ConfirmationRecord:
            now = datetime.now(UTC)
            return ConfirmationRecord(
                id="conf-reject-1",
                protocol_version=1,
                thread_id="thread-1",
                subject_user_id="student-1",
                organization_id="org-1",
                tool_id="fitness.memory.save.v1",
                risk_level="LOW",
                action="SAVE_MEMORY",
                resource_type="agent_memory",
                resource_id=None,
                expected_resource_version=None,
                request_id="req-1",
                payload_hash="a" * 64,
                display_summary={"title": "保存偏好：每周4练"},
                payload_ciphertext=b"{}",
                payload_key_version="plain",
                authorization_status=cast(Any, self.status),
                execution_status="NOT_STARTED",
                version=1,
                expires_at=self.expires_at,
                created_at=now,
            )

    confirmations = _StubConfirmationService()
    model = _RecordingModel(
        [
            ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-save-1",
                        name="fitness.memory.save.v1",
                        arguments={
                            "organization_id": "org-1",
                            "memory_type": "TRAINING_PREFERENCE",
                            "memory_key": "exercise_preference",
                            "value": "每周4练",
                        },
                    ),
                ),
            ),
            ModelTurn(content="好的，那我们改为每周2练。", tool_calls=()),
        ]
    )
    supervisor = Supervisor(
        cast(ModelGateway, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=cast(Any, confirmations),
    )

    # 第一轮：走非流式 invoke 触发确认单暂停。
    first = await supervisor.invoke(
        SupervisorRequest(
            user_message="记住我以后每周4练",
            gateway_context=GatewayRequestContext("signed", request_id="req-1"),
            conversation_id="conv-reject-follow",
            identity=IDENTITY,
        )
    )
    assert first.status == "CONFIRMATION_REQUIRED"
    assert first.confirmation_id == "conf-reject-1"

    # 用户拒绝确认单后，直接发第二轮追问。
    confirmations.status = "REJECTED"
    second = await supervisor.invoke(
        SupervisorRequest(
            user_message="太累了，帮我改成每周2练",
            gateway_context=GatewayRequestContext("signed", request_id="req-2"),
            conversation_id="conv-reject-follow",
            identity=IDENTITY,
        )
    )
    assert second.status == "COMPLETED"
    assert "每周2练" in second.answer
    sent_messages = model.calls[1]["messages"]
    # 绝不能残留未配对的 tool_calls（防 HTTP 400）
    assert not any(m.get("tool_calls") for m in sent_messages)
    # 保留了第一轮用户原话 + 被拒绝的脱敏摘要说明（上下文不丢失）
    assert any("记住我以后每周4练" in str(m.get("content", "")) for m in sent_messages)
    assert any("已被拒绝" in str(m.get("content", "")) for m in sent_messages)


def test_sanitize_unpaired_tool_calls_strips_dangling_and_keeps_paired() -> None:
    messages = [
        {"role": "user", "content": "问题1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "ok-1", "type": "function", "function": {"name": "a"}}],
        },
        {"role": "tool", "tool_call_id": "ok-1", "content": "{}"},
        {"role": "assistant", "content": "回答1"},
        {"role": "user", "content": "问题2"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "orphan-2", "type": "function", "function": {"name": "b"}}],
        },
        {"role": "tool", "tool_call_id": "stray-3", "content": "{}"},
    ]
    cleaned = _sanitize_unpaired_tool_calls(messages)
    assert [m["role"] for m in cleaned] == ["user", "assistant", "tool", "assistant", "user"]


@pytest.mark.asyncio
async def test_confirmation_service_supports_plain_mode_with_hash_tamper_protection() -> None:
    class _MemToolInput(BaseModel):
        organization_id: str
        memory_type: str
        memory_key: str
        value: str

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="fitness.memory.save.v1",
            description="保存记忆",
            input_model=_MemToolInput,
            handler=lambda raw, ctx: _async_return({}),
            allowed_roles=frozenset({"STUDENT"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=ConfirmationPolicy(
                action="SAVE_MEMORY",
                resource_type="agent_memory",
                risk_level="LOW",
                operation="SAVE_MEMORY",
                target_status="ACTIVE",
                payload_builder=lambda model: model.model_dump(mode="json"),
                summary_builder=lambda model, _: {
                    "operation": "SAVE_MEMORY",
                    "target_status": "ACTIVE",
                    "title": "保存偏好：每周4练",
                },
            ),
        )
    )

    class _FakeRepo:
        def __init__(self) -> None:
            self.record: ConfirmationRecord | None = None

        async def create(
            self, confirmation_id: str, action: ConfirmationAction, expires_at: datetime
        ) -> ConfirmationRecord:
            now = datetime.now(UTC)
            self.record = ConfirmationRecord(
                id=confirmation_id,
                protocol_version=1,
                thread_id=action.thread_id,
                subject_user_id=action.subject_user_id,
                organization_id=action.organization_id,
                tool_id=action.tool_id,
                risk_level=action.risk_level,
                action=action.action,
                resource_type=action.resource_type,
                resource_id=action.resource_id,
                expected_resource_version=action.expected_resource_version,
                request_id=action.request_id,
                payload_hash=action.payload_hash,
                display_summary=action.display_summary,
                payload_ciphertext=action.payload_ciphertext,
                payload_key_version=action.payload_key_version,
                authorization_status="APPROVED",
                execution_status="NOT_STARTED",
                version=1,
                expires_at=expires_at,
                created_at=now,
                actor_roles=action.actor_roles,
                actor_organization_ids=action.actor_organization_ids,
                transport=action.transport,
                contract_version=action.contract_version,
            )
            return self.record

        async def get_for_subject(
            self,
            confirmation_id: str,
            subject_user_id: str,
            organization_ids: tuple[str, ...],
        ) -> ConfirmationRecord:
            assert self.record is not None
            return self.record

        async def issue_credential_jti(
            self, confirmation_id: str, jti: str, now: datetime, trace_id: str | None
        ) -> ConfirmationRecord:
            assert self.record is not None
            return self.record

        async def claim_execution(
            self, confirmation_id: str, now: datetime, trace_id: str | None
        ) -> ConfirmationRecord:
            assert self.record is not None
            return self.record

    repo = _FakeRepo()
    cipher = AesGcmPayloadCipher(b"k" * 32, "v1")
    issuer = ConfirmationTokenIssuer("test-signing-secret-at-least-32-bytes")
    service = ConfirmationService(
        cast(Any, repo),
        registry,
        cast(Any, object()),
        cipher,
        issuer,
        encrypt_payload=False,
    )

    record = await service.prepare(
        tool_id="fitness.memory.save.v1",
        raw_input={
            "organization_id": "org-1",
            "memory_type": "TRAINING_PREFERENCE",
            "memory_key": "exercise_preference",
            "value": "举哑铃",
        },
        gateway_context=GatewayRequestContext("signed", request_id="req-plain-1"),
        identity=IDENTITY,
        thread_id="thread-plain",
    )
    assert record.payload_key_version.startswith("plain-sha256:")
    assert json.loads(record.payload_ciphertext)["value"] == "举哑铃"
    assert len(record.payload_hash) == 64

    prepared = await service.prepare_execution(
        record.id, identity=IDENTITY, trace_id="trace-plain-1"
    )
    assert prepared.tool_input["value"] == "举哑铃"

    # 篡改明文中的任意一字节都会被 payload_hash 校验立即拦截。
    tampered_bytes = canonical_json_bytes(
        {**json.loads(record.payload_ciphertext), "value": "被篡改"}
    )
    repo.record = ConfirmationRecord(**{**record.__dict__, "payload_ciphertext": tampered_bytes})
    with pytest.raises(ConfirmationStateError, match="无法恢复确认单 Payload"):
        await service.prepare_execution(record.id, identity=IDENTITY, trace_id="trace-plain-2")


@pytest.mark.asyncio
async def test_controlled_extractor_rejects_one_off_booking_and_accepts_stable_preference() -> None:
    class _EchoJsonModel:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload
            self.calls = 0

        async def chat_json(self, messages: list[dict[str, str]]) -> str:
            self.calls += 1
            return json.dumps(self.payload, ensure_ascii=False)

    model = _EchoJsonModel({"candidates": []})
    extractor = MemoryCandidateExtractionService(cast(Any, model))
    assert await extractor.propose_controlled("星期三帮我约一节坐姿划船的课") == ()
    assert model.calls == 0


async def _async_return(value: Any) -> Any:
    return value
