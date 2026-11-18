"""RAG lab tests exercise the service and prove an injected ACL fault is observed."""

import pytest

from app.evaluation.task_eval.retrieval_lab import run_lab


@pytest.mark.asyncio
async def test_retrieval_lab_is_explicit_about_scope_and_unknowns():
    report = await run_lab()
    assert report["scope"] == "rag_service_retrieval_component_only"
    assert report["repository"] == "substitute"
    cases = {row["case_id"]: row for row in report["cases"]}
    assert cases["local-warmup"]["retrieval"]["status"] == "PASS"
    assert "warmup" in cases["local-warmup"]["retrieved_ids"]
    assert cases["local-breathing"]["retrieval"]["status"] == "PASS"
    assert cases["local-unanswerable"]["retrieval"]["status"] == "NA"
    assert all(row["acl"]["status"] == "UNKNOWN" for row in cases.values())
    assert all(row["answer"]["status"] == "UNKNOWN" for row in cases.values())


@pytest.mark.asyncio
async def test_bypassed_acl_is_detected_from_candidates():
    report = await run_lab(bypass_acl=True)
    row = next(row for row in report["cases"] if row["case_id"] == "local-cross-org-role")
    assert row["acl"]["status"] == "FAIL"
    assert {"foreign", "finance"} <= set(row["forbidden_hits"])
    assert set(row["forbidden_hits"]) <= set(row["vector_candidate_ids"])


@pytest.mark.asyncio
async def test_candidate_leak_is_preserved_when_reranker_errors():
    from app.evaluation.task_eval.component_retrieval import evaluate_retrieval
    from app.evaluation.task_eval.retrieval_lab import CASES, _Models, _Repository
    from app.rag.service import RagService

    class BrokenReranker:
        async def rerank(self, *args, **kwargs):
            raise RuntimeError("reranker unavailable")

    service = RagService(_Repository(bypass_acl=True), _Models(), BrokenReranker())
    result = await evaluate_retrieval(service, [CASES[-1]])
    row = result["cases"][0]
    assert row["retrieval"]["status"] == "ERROR"
    assert row["acl"]["status"] == "FAIL"
    assert {"foreign", "finance"} <= set(row["forbidden_hits"])
