from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.rag import router
from app.infrastructure.agent_context import AgentIdentity
from app.rag.models import KnowledgeChunk
from app.rag.service import RagSearchResult


class FakeVerifier:
    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("user-1", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 2)


class FakeRag:
    async def search(self, query: str, scope: object) -> RagSearchResult:
        assert query == "如何热身"
        return RagSearchResult(
            (
                KnowledgeChunk(
                    id="chunk-1",
                    document_id="doc-1",
                    chunk_index=2,
                    content="先进行动态热身。",
                    source_uri="knowledge://fitness/warmup.md",
                    title="热身指南",
                    document_type="FITNESS_GUIDE",
                    version=3,
                    similarity=0.88,
                    metadata={"heading_path": ["热身"]},
                ),
            )
        )


def build_app() -> FastAPI:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier()
    app.state.rag_service = FakeRag()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    return app


async def test_rag_search_returns_citation_without_accepting_identity_fields() -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/knowledge/search",
            headers={"X-Agent-Context": "signed-context"},
            json={"query": "如何热身", "organization_id": "must-not-be-trusted"},
        )

    assert response.status_code == 422


async def test_rag_search_returns_authorized_citation() -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/knowledge/search",
            headers={"X-Agent-Context": "signed-context"},
            json={"query": "如何热身"},
        )

    assert response.status_code == 200
    assert response.json()["citations"][0]["source_uri"] == "knowledge://fitness/warmup.md"
