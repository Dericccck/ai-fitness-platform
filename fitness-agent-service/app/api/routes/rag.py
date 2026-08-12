"""带权限控制的 RAG 检索与引用 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.rag.models import KnowledgeCitation, RetrievalScope
from app.rag.service import RagSearchError

router = APIRouter(prefix="/api/v1/agent/knowledge", tags=["knowledge"])


class KnowledgeSearchRequest(BaseModel):
    """检索输入；身份和 ACL 过滤字段会被有意排除。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)


class KnowledgeCitationResponse(BaseModel):
    """供 UI 渲染和答案追溯使用的引用载荷。"""

    citation_id: str
    title: str
    source_uri: str
    document_type: str
    version: int
    chunk_index: int
    section_path: tuple[str, ...]
    source_page: int | None
    source_sheet: str | None
    table_index: int | None
    row_start: int | None
    row_end: int | None
    snippet: str
    score: float


class KnowledgeSearchResponse(BaseModel):
    citations: tuple[KnowledgeCitationResponse, ...]


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeSearchResponse:
    """只返回经过服务端权限过滤后生成的引用。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        result = await request.app.state.rag_service.search(
            payload.query,
            RetrievalScope(
                subject=identity.subject,
                organization_ids=identity.organization_ids,
                roles=identity.roles,
            ),
        )
    except RagSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge retrieval is temporarily unavailable",
        ) from exc
    return KnowledgeSearchResponse(
        citations=tuple(_to_response(citation) for citation in result.citations())
    )


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signed agent context is required"
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signed agent context"
        ) from exc


def _to_response(citation: KnowledgeCitation) -> KnowledgeCitationResponse:
    return KnowledgeCitationResponse(**citation.__dict__)
