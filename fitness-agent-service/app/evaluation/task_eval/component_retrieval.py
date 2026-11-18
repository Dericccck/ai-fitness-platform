"""Observe actual RagService retrieval without claiming an Agent answer was tested.

A fresh service is shallow-copied for each case so its repository can be wrapped
without modifying a shared service. Capturing candidates matters: a forbidden chunk
must be detected even when the reranker drops it from the final result.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.rag.models import RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.rag.service import RagService

from .graders.retrieval import grade_retrieval


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    scope: RetrievalScope
    relevant_ids: tuple[str, ...]
    forbidden_ids: tuple[str, ...] = ()
    answerable: bool = True
    k: int = 5


class _ObservedRepository:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.vector_ids: list[str] | None = None
        self.keyword_ids: list[str] | None = None

    async def search_candidates(self, *args: Any, **kwargs: Any) -> Any:
        chunks = await self.delegate.search_candidates(*args, **kwargs)
        self.vector_ids = [chunk.id for chunk in chunks]
        return chunks

    async def search_keyword_candidates(self, *args: Any, **kwargs: Any) -> Any:
        chunks = await self.delegate.search_keyword_candidates(*args, **kwargs)
        self.keyword_ids = [chunk.id for chunk in chunks]
        return chunks

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


async def evaluate_retrieval(service: RagService, cases: list[RetrievalCase]) -> dict[str, Any]:
    """Evaluate observable retrieval only; expected labels are caller-provided drafts.

    Local substitutes and real repositories can expose leaks, but absence of a
    forbidden result cannot prove ACL without verifying that the forbidden
    positive-control documents actually existed and could have been recalled.
    """
    rows: list[dict[str, Any]] = []
    real_repository = isinstance(service.repository, KnowledgeRepository)
    for case in cases:
        observed = _ObservedRepository(service.repository)
        current = copy.copy(service)
        current.repository = observed  # type: ignore[assignment]
        try:
            result = await current.search(case.query, case.scope)
            final_ids = [chunk.id for chunk in result.chunks]
            if observed.vector_ids is None or observed.keyword_ids is None:
                raise RuntimeError("candidate observations unavailable")
            candidates = list(dict.fromkeys(observed.vector_ids + observed.keyword_ids))
            relevance = grade_retrieval(
                {
                    "answerable": case.answerable,
                    "relevant_ids": list(case.relevant_ids),
                    "forbidden_ids": [],
                    "k": case.k,
                },
                {"retrieved_ids": final_ids, "evidence_refs": ["rag-service-search"]},
            )
            forbidden_hits = sorted(set(candidates + final_ids) & set(case.forbidden_ids))
            if not case.forbidden_ids:
                acl = {"status": "UNKNOWN", "reason": "NO_FORBIDDEN_CONTROL"}
            elif forbidden_hits:
                acl = {"status": "FAIL", "reason": "FORBIDDEN_CANDIDATE_OBSERVED"}
            elif real_repository:
                acl = {"status": "UNKNOWN", "reason": "FORBIDDEN_CONTROL_NOT_VERIFIED"}
            else:
                acl = {"status": "UNKNOWN", "reason": "SIMULATED_REPOSITORY_ACL"}
            rows.append(
                {
                    "case_id": case.case_id,
                    "retrieved_ids": final_ids,
                    "vector_candidate_ids": observed.vector_ids,
                    "keyword_candidate_ids": observed.keyword_ids,
                    "forbidden_hits": forbidden_hits,
                    "retrieval": relevance,
                    "acl": acl,
                    "answer": {"status": "UNKNOWN", "reason": "AGENT_ANSWER_NOT_EVALUATED"},
                }
            )
        except Exception:  # noqa: BLE001 - no credentials or corpus text in output
            candidates = (observed.vector_ids or []) + (observed.keyword_ids or [])
            forbidden_hits = sorted(set(candidates) & set(case.forbidden_ids))
            rows.append(
                {
                    "case_id": case.case_id,
                    "vector_candidate_ids": observed.vector_ids,
                    "keyword_candidate_ids": observed.keyword_ids,
                    "forbidden_hits": forbidden_hits,
                    "retrieval": {"status": "ERROR", "reason": "RETRIEVAL_ERROR"},
                    "acl": (
                        {"status": "FAIL", "reason": "FORBIDDEN_CANDIDATE_OBSERVED"}
                        if forbidden_hits
                        else {"status": "UNKNOWN", "reason": "RETRIEVAL_ERROR"}
                    ),
                    "answer": {"status": "UNKNOWN", "reason": "AGENT_ANSWER_NOT_EVALUATED"},
                }
            )
    return {
        "scope": "rag_service_retrieval_component_only",
        "labels": "draft_unreviewed",
        "repository": "postgres_knowledge_repository" if real_repository else "substitute",
        "cases": rows,
    }
