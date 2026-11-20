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
