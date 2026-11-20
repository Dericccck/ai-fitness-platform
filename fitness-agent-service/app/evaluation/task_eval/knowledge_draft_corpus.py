"""Hash-bound synthetic knowledge fixtures and a retrieval-only component probe.

Nothing here establishes clinical safety, production policy, real ACLs, or an
Agent answer. A document's answerability is scoped to its per-case draft corpus.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from app.rag.models import RetrievalScope
from app.rag.service import RagService

from .component_retrieval import RetrievalCase, evaluate_retrieval
from .dataset import Dataset
from .knowledge_dataset_local import _Document, _Repository
from .retrieval_lab import _Models, _Reranker

_PATH = (
    Path(__file__).resolve().parents[3] / "evals/task_eval/draft_knowledge_fixture_v1/corpus.json"
)
CORPUS_SHA256 = "a76bfb1168b110db62c6146a6b40e35652d7c33f44a3dc546547c68a71b1feb8"
_SOURCE_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_SOURCE_CASES_SHA256 = "fb2b0533fe6e0512332f1ec577cdb3e5bcd4bb9efdd0de729f94edf845b1b664"


def load_corpus(dataset: Dataset) -> dict[str, dict[str, Any]]:
    serialized = json.dumps(
        [c.model_dump(mode="json") for c in dataset.cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if (
        dataset.sha256 != _SOURCE_SHA256
        or hashlib.sha256(serialized).hexdigest() != _SOURCE_CASES_SHA256
    ):
        raise ValueError("SOURCE_DATASET_CHANGED")
    raw = _PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CORPUS_SHA256:
        raise ValueError("KNOWLEDGE_CORPUS_CHANGED")
    corpus = json.loads(raw)
    if (
        corpus.get("schema_version") != "1.0"
        or corpus.get("version") != "synthetic-knowledge-draft-v1"
        or corpus.get("status") != "synthetic_unreviewed_not_for_user_guidance"
        or corpus.get("policy")
        != {"top_k": 1, "min_recall_at_k": 1.0, "scope": "isolated_per_case"}
    ):
        raise ValueError("KNOWLEDGE_CORPUS_INVALID")
    cases = {case.case_id: case for case in dataset.cases}
    entries = corpus.get("cases")
    if not isinstance(entries, list) or len(entries) != 10:
        raise ValueError("KNOWLEDGE_CORPUS_INVALID")
    result = {}
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "case_id",
            "split",
            "document_id",
            "title",
            "content",
        }:
            raise ValueError("KNOWLEDGE_CORPUS_INVALID")
        cid, docid = entry["case_id"], entry["document_id"]
        case = cases.get(cid)
        if (
            not isinstance(cid, str)
            or cid in result
            or case is None
            or case.primary_scenario != "knowledge"
            or case.subcategory != "answerable"
            or case.review.status != "draft"
            or case.source.type != "synthetic"
            or entry["split"] != case.split
            or not isinstance(docid, str)
            or not docid.startswith(f"draft-{cid.lower()}-")
            or docid in seen_ids
            or not all(isinstance(entry[k], str) and entry[k].strip() for k in ("title", "content"))
            or len(case.steps) != 2
            or case.steps[0].type != "chat"
            or not case.steps[0].message
            or case.steps[0].message in entry["content"]
        ):
            raise ValueError("KNOWLEDGE_CASE_MISMATCH")
        result[cid] = entry
        seen_ids.add(docid)
    if set(result) != {f"KNOW-{i:03d}" for i in range(1, 11)}:
        raise ValueError("KNOWLEDGE_CORPUS_INVALID")
    return result


async def evaluate_draft_corpus(
    dataset: Dataset, *, case_ids: frozenset[str] | None = None
) -> dict[str, Any]:
    entries = load_corpus(dataset)
    if case_ids is not None:
        if not case_ids <= entries.keys():
            raise ValueError("KNOWLEDGE_CASE_SELECTION_INVALID")
        entries = {cid: entry for cid, entry in entries.items() if cid in case_ids}
    cases = {c.case_id: c for c in dataset.cases}
    rows = []
    for cid, entry in entries.items():
        case = cases[cid]
        # Separate fixture per case; substitute ACL, embeddings, and reranking.
        docs = (
            _Document(entry["document_id"], entry["title"] + "：" + entry["content"]),
            _Document(f"draft-{cid.lower()}-decoy-v1", "器材保管说明：训练用品使用后放回原位。"),
        )
        service = RagService(_Repository(docs), _Models(), _Reranker(), top_k=1)  # type: ignore[arg-type]
        result = await evaluate_retrieval(
            service,
            [
                RetrievalCase(
                    cid,
                    cast(str, case.steps[0].message),
                    RetrievalScope("actor_a", frozenset({"org_a"}), frozenset({"STUDENT"})),
                    (entry["document_id"],),
                    answerable=True,
                    k=1,
                )
            ],
        )
        row = result["cases"][0]
        row["split"] = case.split
        row["task_status"] = "UNKNOWN"
        rows.append(row)
    return {
        "origin": "synthetic_rag_component_with_local_substitutes",
        "corpus_sha256": CORPUS_SHA256,
        "status": "draft_unreviewed_not_release_evidence",
        "limitations": [
            "No Agent answer, professional review, production retrieval, or actual ACL was evaluated.",
            "Test data and relevance are developer-visible.",
        ],
        "cases": rows,
    }


def main() -> int:
    import argparse

    from .dataset import load_dataset

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(evaluate_draft_corpus(load_dataset(args.dataset)))
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    failures = sum(row["retrieval"]["status"] != "PASS" for row in result["cases"])
    print(json.dumps({"cases": len(result["cases"]), "retrieval_nonpass": failures}))
    return 2 if failures else 3  # Component-only: the Agent task remains unknown.


if __name__ == "__main__":
    raise SystemExit(main())
