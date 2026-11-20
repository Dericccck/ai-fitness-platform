"""Request-scoped retrieval metadata; fake providers here are not evaluation evidence."""

import asyncio
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.supervisor import Supervisor, SupervisorRequest
from app.agent.tool_registry import ToolRegistry
from app.api.routes.agent import AgentChatResponse
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway, ModelTurn
from app.infrastructure.reranker import RerankResult
from app.rag.models import KnowledgeChunk, RetrievalScope
from app.rag.service import RagService


class Model:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        await asyncio.sleep(0)
        return ModelTurn(content="测试回答", tool_calls=())


class Repository:
    def __init__(self) -> None:
        self.scopes: list[RetrievalScope] = []
        self.return_empty = False

    async def search_candidates(
        self, embedding: list[float], scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        self.scopes.append(scope)
        await asyncio.sleep(0)
        if self.return_empty or scope.subject == "empty":
            return []
        return [
            KnowledgeChunk(
                id=f"chunk-{scope.subject}",
                document_id=f"doc-{scope.subject}",
                chunk_index=0,
                content="合成测试材料",
                source_uri="fixture://synthetic",
                title="测试材料",
                document_type="SYNTHETIC",
                version=1,
                similarity=1.0,
                metadata={},
            )
        ]

    async def search_keyword_candidates(
        self, query: str, scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        return []


class Reranker:
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        return [RerankResult(i, 1.0, doc) for i, doc in enumerate(documents)]


def request(subject: str | None, *, conversation: str) -> SupervisorRequest:
    identity = (
        AgentIdentity(subject, frozenset({f"org-{subject}"}), frozenset({"STUDENT"}), 1, 99)
        if subject is not None
        else None
    )
    return SupervisorRequest(
        user_message="力量训练前如何热身？",
        gateway_context=GatewayRequestContext("synthetic-context"),
        conversation_id=conversation,
        identity=identity,
    )


@pytest.mark.asyncio
async def test_retrieved_ids_are_scope_bound_and_isolated_between_concurrent_requests() -> None:
    repository, model = Repository(), Model()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    checkpointer = InMemorySaver()
    supervisor = Supervisor(
        cast(ModelGateway, model), ToolRegistry(), rag_service=rag, checkpointer=checkpointer
    )
    first, second = await asyncio.gather(
        supervisor.invoke(request("alice", conversation="conversation-a")),
        supervisor.invoke(request("bob", conversation="conversation-b")),
    )
    assert first.retrieved_evidence_ids == ("chunk-alice",)
    assert second.retrieved_evidence_ids == ("chunk-bob",)
    assert {scope.subject for scope in repository.scopes} == {"alice", "bob"}
    assert {tuple(scope.organization_ids) for scope in repository.scopes} == {
        ("org-alice",),
        ("org-bob",),
    }
    for conversation in ("conversation-a", "conversation-b"):
        checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": conversation}})
        assert checkpoint is not None
        assert "retrieved_evidence_ids" not in checkpoint.checkpoint["channel_values"]


@pytest.mark.asyncio
async def test_zero_hits_missing_identity_and_missing_rag_do_not_fabricate_evidence() -> None:
    repository, model = Repository(), Model()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(cast(ModelGateway, model), ToolRegistry(), rag_service=rag)
    empty = await supervisor.invoke(request("empty", conversation="empty"))
    before = len(repository.scopes)
    no_identity = await supervisor.invoke(request(None, conversation="anonymous"))
    no_rag = await Supervisor(cast(ModelGateway, model), ToolRegistry()).invoke(
        request("alice", conversation="no-rag")
    )
    assert empty.retrieved_evidence_ids == ()
    assert no_identity.retrieved_evidence_ids == ()
    assert no_rag.retrieved_evidence_ids == ()
    assert len(repository.scopes) == before


def test_empty_metadata_is_omitted_from_existing_api_contract() -> None:
    response = AgentChatResponse(
        conversation_id="c",
        answer="a",
        route="FITNESS_COACHING",
        tool_steps=0,
        input_tokens=None,
        output_tokens=None,
    )
    assert "retrieved_evidence_ids" not in response.model_dump(exclude_defaults=True)


@pytest.mark.asyncio
async def test_retrieval_ids_do_not_survive_into_a_later_turn() -> None:
    repository, model = Repository(), Model()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(
        cast(ModelGateway, model), ToolRegistry(), rag_service=rag, checkpointer=InMemorySaver()
    )
    first = await supervisor.invoke(request("alice", conversation="one-thread"))
    repository.return_empty = True
    second = await supervisor.invoke(request("alice", conversation="one-thread"))
    assert first.retrieved_evidence_ids == ("chunk-alice",)
    assert second.retrieved_evidence_ids == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("资料说先热身。[证据1]", "资料说先热身。[证据1]"),
        ("资料说先热身。", "本次回答未提供可核验的知识引用，请重试或咨询专业人员。"),
        ("资料说先热身。[证据2]", "本次回答未提供可核验的知识引用，请重试或咨询专业人员。"),
        (
            "资料说先热身。[证据1][证据999]",
            "本次回答未提供可核验的知识引用，请重试或咨询专业人员。",
        ),
        ("资料说先热身。[证据x]", "本次回答未提供可核验的知识引用，请重试或咨询专业人员。"),
    ],
)
def test_citation_validation_is_limited_to_actual_retrieval_ids(text, expected) -> None:
    from app.agent.supervisor import _verified_knowledge_answer

    assert _verified_knowledge_answer(text, ["chunk-1"]) == expected
    assert (
        _verified_knowledge_answer(text, [])
        == "未检索到可用的知识资料，暂时无法提供有来源支持的回答。"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_answer", "expected"),
    [
        ("先热身。[证据1]", "先热身。[证据1]"),
        ("无引用的内容", "本次回答未提供可核验的知识引用，请重试或咨询专业人员。"),
        ("伪造来源。[证据9]", "本次回答未提供可核验的知识引用，请重试或咨询专业人员。"),
    ],
)
async def test_stream_buffers_model_output_until_knowledge_citations_are_checked(
    model_answer, expected
) -> None:
    from app.streaming.events import emit_event

    class StreamingModel(Model):
        async def chat_with_tools(self, messages, **kwargs):
            emit_event("answer.delta", {"text": model_answer})
            return ModelTurn(content=model_answer, tool_calls=())

    repository, model = Repository(), StreamingModel()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(cast(ModelGateway, model), ToolRegistry(), rag_service=rag)
    events = []

    async def emit(event, data):
        events.append((event, data))

    result = await supervisor.stream(request("alice", conversation="stream"), emit)
    assert result.answer == expected
    assert result.knowledge_citation_evidence_ids == ("chunk-alice",)
    assert [data["text"] for event, data in events if event == "answer.delta"] == [expected]


@pytest.mark.asyncio
async def test_failed_model_call_does_not_stream_unvalidated_knowledge_answer() -> None:
    from app.streaming.events import emit_event

    class BrokenModel(Model):
        async def chat_with_tools(self, messages, **kwargs):
            emit_event("answer.delta", {"text": "未验证的内容"})
            raise RuntimeError("failure")

    repository, model = Repository(), BrokenModel()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(cast(ModelGateway, model), ToolRegistry(), rag_service=rag)
    events = []

    async def emit(event, data):
        events.append((event, data))

    with pytest.raises(RuntimeError, match="failure"):
        await supervisor.stream(request("alice", conversation="broken"), emit)
    assert not [data for event, data in events if event == "answer.delta"]


@pytest.mark.asyncio
async def test_cancelled_model_never_streams_unverified_text() -> None:
    from app.streaming.events import emit_event, streaming_enabled

    started = asyncio.Event()

    class CancelledModel(Model):
        async def chat_with_tools(self, messages, **kwargs):
            emit_event("answer.delta", {"text": "未验证的内容"})
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    repository, model = Repository(), CancelledModel()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(cast(ModelGateway, model), ToolRegistry(), rag_service=rag)
    events = []

    async def emit(event, data):
        events.append((event, data))

    task = asyncio.create_task(supervisor.stream(request("alice", conversation="cancelled"), emit))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not [data for event, data in events if event == "answer.delta"]
    assert not streaming_enabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_two_rag_domains_do_not_publish_an_ambiguous_marker_mapping(reverse) -> None:
    from app.agent.routing import RouteDecision, RoutedIntent

    class Router:
        async def decide(self, user_message, *, recent_messages, previous_route):
            items = (
                RoutedIntent("FITNESS_COACHING", "FITNESS_KNOWLEDGE_QA"),
                RoutedIntent("CUSTOMER_SERVICE", "BUSINESS_RULE_QA"),
            )
            return RouteDecision(
                intents=tuple(reversed(items)) if reverse else items,
                source="DETERMINISTIC_RULE",
                confidence=0.99,
                reason_code="TEST_MULTI",
                classifier_version="test",
            )

    class CitingModel(Model):
        async def chat_with_tools(self, messages, **kwargs):
            return ModelTurn(content="内容[证据1]", tool_calls=())

    repository, model = Repository(), CitingModel()
    rag = RagService(cast(Any, repository), cast(Any, model), cast(Any, Reranker()))
    supervisor = Supervisor(
        cast(ModelGateway, model), ToolRegistry(), rag_service=rag, intent_router=Router()
    )
    response = await supervisor.invoke(request("alice", conversation="multi-rag"))
    assert "本次回答的知识来源无法可靠对应" in response.answer
    assert "[证据1]" not in response.answer
    assert response.knowledge_citation_evidence_ids == ()
    assert len(repository.scopes) == 2
