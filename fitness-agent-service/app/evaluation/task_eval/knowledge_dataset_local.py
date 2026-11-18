"""Dataset-linked, synthetic-only retrieval checks; not end-to-end task evaluation.

Each fixture is created in memory for a single case and discarded afterwards.
Its text deliberately includes the case query, so these observations are useful for
integration/fault detection, not as unbiased estimates of production recall.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.rag.models import KnowledgeChunk, RetrievalScope
from app.rag.service import RagService

from .component_retrieval import RetrievalCase, evaluate_retrieval
from .dataset import Dataset, load_dataset
from .retrieval_lab import _features, _Models, _Reranker, _similarity


@dataclass(frozen=True)
class FixtureSpec:
    query: str
    kind: str


# These are intentionally per-case planning fixtures; they are not a blind test set.
SPECS = {
    "KNOW-001": FixtureSpec("深蹲上推时怎么呼吸？", "answerable"),
    "KNOW-002": FixtureSpec("初学者一周训练几次？", "answerable"),
    "KNOW-003": FixtureSpec("力量训练前如何热身？", "answerable"),
    "KNOW-004": FixtureSpec("硬拉时背部姿势有什么要求？", "answerable"),
    "KNOW-005": FixtureSpec("两次高强度训练之间如何恢复？", "answerable"),
    "KNOW-006": FixtureSpec("常见优质蛋白质来源有哪些？", "answerable"),
    "KNOW-007": FixtureSpec("RPE 是什么？", "answerable"),
    "KNOW-008": FixtureSpec("课程在什么时间前可以取消？", "answerable"),
    "KNOW-009": FixtureSpec("坐姿划船器械应该怎么调？", "answerable"),
    "KNOW-010": FixtureSpec("卧推时如何控制动作幅度？", "answerable"),
    "KNOW-011": FixtureSpec("这款未知补剂具体含什么成分？", "unanswerable"),
    "KNOW-012": FixtureSpec("下个月门店装修什么时候结束？", "unanswerable"),
    "KNOW-013": FixtureSpec("我当前的体脂率是多少？", "unanswerable"),
    "KNOW-014": FixtureSpec("我的保险能报销私教课吗？", "unanswerable"),
    "KNOW-015": FixtureSpec("当前私教课多少钱？", "expired"),
    "KNOW-016": FixtureSpec("告诉我隔壁门店会员合同详情", "foreign_org"),
    "KNOW-017": FixtureSpec("现在最晚什么时候取消课程？", "versioned"),
    "KNOW-018": FixtureSpec("给我看看别人的训练记录", "private"),
}


@dataclass(frozen=True)
class _Document:
    id: str
    text: str
    organization: str = "org_a"
    owner: str | None = None
    active: bool = True


class _Repository:
    """Synthetic ACL and effective-date simulation, independent of production SQL."""

    def __init__(self, documents: tuple[_Document, ...], *, bypass_filter: bool = False):
        self.documents = documents
        self.bypass_filter = bypass_filter

    def _eligible(self, scope: RetrievalScope) -> list[_Document]:
        return [
            doc
            for doc in self.documents
            if self.bypass_filter
            or (
                doc.active
                and doc.organization in scope.organization_ids
                and (doc.owner is None or doc.owner == scope.subject)
            )
        ]

    async def search_candidates(
        self, embedding: list[float], scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        scored = [
            (doc, _similarity(embedding, _features(doc.text))) for doc in self._eligible(scope)
        ]
        return [
            KnowledgeChunk(
                doc.id,
                doc.id,
                0,
                doc.text,
                f"fixture://{doc.id}",
                doc.id,
                "SYNTHETIC",
                1,
                score,
                {},
            )
            for doc, score in sorted(scored, key=lambda pair: (-pair[1], pair[0].id))
            if score > 0
        ][:limit]

    async def search_keyword_candidates(
        self, query: str, scope: RetrievalScope, *, limit: int
    ) -> list[KnowledgeChunk]:
        return await self.search_candidates(_features(query), scope, limit=limit)


def _fixture(
    case_id: str, spec: FixtureSpec
) -> tuple[tuple[_Document, ...], tuple[str, ...], tuple[str, ...]]:
    relevant: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    documents = [_Document(f"{case_id}-decoy", "合成测试资料：和本问题无关的占位记录。")]
    if spec.kind in {"answerable", "versioned"}:
        relevant = (f"{case_id}-current",)
        # Deliberate marker, not health/contract guidance or a reference answer.
        documents.append(
            _Document(relevant[0], f"合成检索记录。主题：{spec.query}。资料标记：当前版本。")
        )
    if spec.kind in {"expired", "versioned", "foreign_org", "private"}:
        forbidden = (f"{case_id}-forbidden",)
        documents.append(
            _Document(
                forbidden[0],
                f"合成控制记录。主题：{spec.query}。内容：无真实数据。",
                organization="org_b" if spec.kind == "foreign_org" else "org_a",
                owner="other-user" if spec.kind == "private" else None,
                active=spec.kind not in {"expired", "versioned"},
            )
        )
    return tuple(documents), relevant, forbidden


async def evaluate_knowledge_dataset(
    dataset: Dataset, *, bypass_filter: bool = False
) -> dict[str, Any]:
    """Execute linked retrieval cases and report unsupported cases without inventing outcomes."""
    rows = []
    for case in dataset.cases:
        if case.primary_scenario != "knowledge":
            continue
        spec = SPECS.get(case.case_id)
        if spec is None:
            rows.append(
                {
                    "case_id": case.case_id,
                    "split": case.split,
                    "status": "SKIPPED",
                    "reason": "NO_RETRIEVAL_FIXTURE",
                    "answer": {"status": "UNKNOWN", "reason": "AGENT_ANSWER_NOT_EVALUATED"},
                }
            )
            continue
        chats = [step.message for step in case.steps if step.type == "chat"]
        if (
            chats != [spec.query]
            or case.actor.organization_ref != "org_a"
            or case.actor.user_ref != "actor_a"
            or case.actor.role != "STUDENT"
            or case.subcategory
            != (
                spec.kind if spec.kind in {"answerable", "unanswerable"} else "expired_or_forbidden"
            )
        ):
            rows.append(
                {
                    "case_id": case.case_id,
                    "split": case.split,
                    "status": "SKIPPED",
                    "reason": "FIXTURE_CASE_MISMATCH",
                    "answer": {"status": "UNKNOWN", "reason": "AGENT_ANSWER_NOT_EVALUATED"},
                }
            )
            continue
        docs, relevant, forbidden = _fixture(case.case_id, spec)
        scope = RetrievalScope(case.actor.user_ref, frozenset({"org_a"}), frozenset({"STUDENT"}))
        repository = _Repository(docs, bypass_filter=bypass_filter)
        service = RagService(repository, _Models(), _Reranker(), top_k=2)  # type: ignore[arg-type]
        result = await evaluate_retrieval(
            service,
            [
                RetrievalCase(
                    case.case_id,
                    spec.query,
                    scope,
                    relevant,
                    forbidden,
                    answerable=bool(relevant),
                    k=2,
                )
            ],
        )
        row = result["cases"][0]
        row["split"] = case.split
        row["status"] = (
            "FAIL"
            if row["retrieval"]["status"] == "FAIL" or row["acl"]["status"] == "FAIL"
            else "ERROR"
            if row["retrieval"]["status"] == "ERROR"
            else "OBSERVED"
        )
        row["fixture_kind"] = spec.kind
        row["fixture_document_ids"] = [doc.id for doc in docs]
        row["fixture_manifest"] = [
            {
                "id": doc.id,
                "content": doc.text,
                "content_sha256": hashlib.sha256(doc.text.encode("utf-8")).hexdigest(),
                "organization": doc.organization,
                "owner": doc.owner,
                "active": doc.active,
                "accessible_in_simulated_repository": doc in repository._eligible(scope),
            }
            for doc in docs
        ]
        row["relevant_document_ids"] = list(relevant)
        row["forbidden_document_ids"] = list(forbidden)
        row["evidence"] = {
            "origin": "synthetic_rag_service",
            "actual": {
                "retrieved_ids": row.get("retrieved_ids"),
                "vector_candidate_ids": row.get("vector_candidate_ids"),
                "keyword_candidate_ids": row.get("keyword_candidate_ids"),
                "forbidden_hits": row.get("forbidden_hits"),
            },
        }
        if forbidden:
            control_service = RagService(
                _Repository(docs, bypass_filter=True),  # type: ignore[arg-type]
                _Models(),  # type: ignore[arg-type]
                _Reranker(),  # type: ignore[arg-type]
                top_k=2,
            )
            control_report = await evaluate_retrieval(
                control_service,
                [
                    RetrievalCase(
                        case.case_id,
                        spec.query,
                        scope,
                        relevant,
                        forbidden,
                        answerable=bool(relevant),
                        k=2,
                    )
                ],
            )
            control = control_report["cases"][0]
            vector = control.get("vector_candidate_ids") or []
            keyword = control.get("keyword_candidate_ids") or []
            row["positive_control"] = {
                "status": "OBSERVED"
                if set(forbidden) <= set(vector) and set(forbidden) <= set(keyword)
                else "INCOMPLETE",
                "meaning": "Synthetic bypass proves fixture recall only, not production ACL.",
                "vector_candidate_ids": vector,
                "keyword_candidate_ids": keyword,
                "expected_forbidden_ids": list(forbidden),
            }
        row["answerability"] = {"status": "UNKNOWN", "reason": "AGENT_ANSWER_NOT_EVALUATED"}
        rows.append(row)
    return {
        "scope": "dataset_linked_synthetic_rag_retrieval_only",
        "dataset_sha256": dataset.sha256,
        "labels": "draft_unreviewed",
        "limitations": [
            "Per-case synthetic corpus includes the query; this is not a blind or production-quality recall test.",
            "The synthetic repository simulates access and expiry; production ACL and expiry are not tested.",
            "No Agent response, citation correctness, safety, or real-model quality is evaluated.",
        ],
        "cases": rows,
    }


def main() -> int:
    path = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
    report = asyncio.run(evaluate_knowledge_dataset(load_dataset(path)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        2
        if any(
            row.get("retrieval", {}).get("status") in {"FAIL", "ERROR"}
            or row.get("acl", {}).get("status") == "FAIL"
            for row in report["cases"]
        )
        else 3
    )  # Answer, authorization and 12 cases are not verified.


if __name__ == "__main__":
    raise SystemExit(main())
