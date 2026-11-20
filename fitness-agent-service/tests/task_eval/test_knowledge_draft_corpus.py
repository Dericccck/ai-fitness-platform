from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import knowledge_draft_corpus as target
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.draft_parameters import build_proposal
from app.evaluation.task_eval.graders.retrieval import grade_retrieval

SOURCE = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def test_case_scoped_labels_and_proposal_are_bound_to_draft_corpus(tmp_path):
    dataset = load_dataset(SOURCE)
    entries = target.load_corpus(dataset)
    assert len(entries) == 10
    assert len({entry["document_id"] for entry in entries.values()}) == 10
    assert [cid for cid, entry in entries.items() if entry["split"] == "test"] == [
        "KNOW-008",
        "KNOW-009",
        "KNOW-010",
    ]
    assert "虚构" in entries["KNOW-008"]["content"]
    raw, manifest = build_proposal(dataset)
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    proposed = {case.case_id: case for case in load_dataset(path).cases}
    for cid, entry in entries.items():
        assertion = proposed[cid].expected.assertions[0]
        assert assertion.parameters["answerable"] is True
        assert assertion.parameters["relevant_ids"] == [entry["document_id"]]
        assert assertion.parameters["k"] == 1
        assert grade_retrieval(assertion.parameters, None)["status"] == "UNKNOWN"
        assert (
            grade_retrieval(
                assertion.parameters, {"retrieved_ids": [], "evidence_refs": ["fixture"]}
            )["status"]
            == "FAIL"
        )
        change = next(c for c in manifest["changes"] if c["case_id"] == cid)
        assert change["fixture_sha256"] == target.CORPUS_SHA256
        assert proposed[cid].review.status == "draft"
    # The separate unanswerable planning cases are not relabeled based on these fixtures.
    for cid in ("KNOW-011", "KNOW-012", "KNOW-013", "KNOW-014"):
        assert cid not in entries


@pytest.mark.asyncio
async def test_actual_rag_service_with_substitutes_reports_retrieval_only():
    report = await target.evaluate_draft_corpus(load_dataset(SOURCE))
    assert len(report["cases"]) == 10
    assert all(row["retrieval"]["status"] == "PASS" for row in report["cases"])
    assert all(
        row["answer"]["status"] == "UNKNOWN" and row["task_status"] == "UNKNOWN"
        for row in report["cases"]
    )
    assert all(row["acl"]["status"] == "UNKNOWN" for row in report["cases"])
    assert report["status"] == "draft_unreviewed_not_release_evidence"


def test_changed_input_or_corpus_never_reuses_relevance_labels(tmp_path, monkeypatch):
    dataset = load_dataset(SOURCE)
    case = dataset.cases[0].model_copy(deep=True)
    case.steps[0].message = "tampered"
    with pytest.raises(ValueError, match="SOURCE_DATASET_CHANGED"):
        target.load_corpus(replace(dataset, cases=[case, *dataset.cases[1:]]))
    path = tmp_path / "corpus.json"
    path.write_bytes(target._PATH.read_bytes() + b" ")
    monkeypatch.setattr(target, "_PATH", path)
    with pytest.raises(ValueError, match="KNOWLEDGE_CORPUS_CHANGED"):
        target.load_corpus(dataset)


@pytest.mark.asyncio
async def test_missing_relevant_document_is_detected(monkeypatch):
    repository = target._Repository

    def without_relevant(documents):
        return repository((documents[1],))

    monkeypatch.setattr(target, "_Repository", without_relevant)
    report = await target.evaluate_draft_corpus(load_dataset(SOURCE))
    assert all(row["retrieval"]["status"] == "FAIL" for row in report["cases"])
    assert all(row["task_status"] == "UNKNOWN" for row in report["cases"])


def test_cli_component_success_does_not_report_full_task_success(tmp_path, monkeypatch):
    import json
    import sys

    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--dataset", str(SOURCE), "--output", str(output)])
    assert target.main() == 3
    report = json.loads(output.read_text())
    assert len(report["cases"]) == 10
    assert all(row["task_status"] == "UNKNOWN" for row in report["cases"])
    with pytest.raises(FileExistsError):
        target.main()


@pytest.mark.asyncio
async def test_selection_does_not_evaluate_other_cases():
    dataset = load_dataset(SOURCE)
    report = await target.evaluate_draft_corpus(dataset, case_ids=frozenset({"KNOW-008"}))
    assert [row["case_id"] for row in report["cases"]] == ["KNOW-008"]
    with pytest.raises(ValueError, match="KNOWLEDGE_CASE_SELECTION_INVALID"):
        await target.evaluate_draft_corpus(dataset, case_ids=frozenset({"KNOW-011"}))


@pytest.mark.asyncio
async def test_each_probe_invokes_fresh_rag_search(monkeypatch):
    calls = []
    original = target.RagService.search

    async def observed(self, query, scope):
        calls.append(query)
        return await original(self, query, scope)

    monkeypatch.setattr(target.RagService, "search", observed)
    dataset = load_dataset(SOURCE)
    for _ in range(2):
        report = await target.evaluate_draft_corpus(dataset, case_ids=frozenset({"KNOW-008"}))
        assert report["cases"][0]["retrieval"]["status"] == "PASS"
    assert len(calls) == 2
