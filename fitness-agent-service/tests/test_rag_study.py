from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.rag.study.contracts import Protocol, digest, read, save, seal, unseal
from app.rag.study.reporting import markdown, summarize
from app.rag.study.runtime import METHODS, SnapshotChanged, StudyRuntime
from app.rag.study.synthetic import generate
from app.rag.study.workflow import (
    assign_splits,
    freeze,
    prepare,
    reconcile,
    review_packet,
    validate_review,
)


@pytest.fixture
def bundle():
    data = generate("study-tests")
    prepared = prepare(data["protocol"], data["questions"], data["scopes"], data["triage"])
    entries = [
        {"id": topic, "content": topic, "title": topic}
        for topic in sorted(
            {item for label in data["fixture_labels"] for item in label["relevant_ids"]}
        )
    ]
    pool = seal(
        "pool",
        {
            "prepared": unseal(prepared, "prepared"),
            "cases": [
                {
                    "case": case,
                    "catalog": entries,
                    "catalog_sha256": digest(entries),
                    "candidate_ids": ["warmup"],
                }
                for case in prepared["payload"]["cases"]
            ],
        },
    )
    a = {
        "pool_sha256": pool["sha256"],
        "reviewer_id": "fixture-A",
        "reviewer_kind": "scripted_fixture",
        "independent_review_declared": True,
        "labels": data["fixture_labels"],
    }
    b = {**copy.deepcopy(a), "reviewer_id": "fixture-B"}
    return data, pool, a, b


def test_synthetic_bank_and_complete_freeze(bundle):
    data, pool, a, b = bundle
    assert len(data["questions"]) == 80
    assert all(row["source_kind"] == "synthetic" for row in data["questions"])
    result = unseal(freeze(pool, reconcile(pool, a, b)), "frozen")
    assert len(result["cases"]) == 80
    assert result["category_counts"] == {
        "answerable": 70,
        "business_tool": 4,
        "no_answer": 4,
        "needs_context": 1,
        "out_of_scope": 1,
    }
    assert {row["split"] for row in result["cases"]} == {"dev", "test"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "pilot"),
        ("consent", False),
        ("consent_version", "wrong"),
        ("collected_at", "2026-01-01"),
    ],
)
def test_collection_contract_rejects_invalid_provenance(bundle, field, value):
    data, *_ = bundle
    data["questions"][0][field] = value
    with pytest.raises(ValueError):
        prepare(data["protocol"], data["questions"], data["scopes"], data["triage"])


def test_no_silent_dropping_and_context_rewrite_requires_basis(bundle):
    data, *_ = bundle
    with pytest.raises(ValueError, match="完整分类"):
        prepare(data["protocol"], data["questions"], data["scopes"], data["triage"][:-1])
    data["triage"][0]["retrieval_query"] = "改写后问题"
    with pytest.raises(ValueError, match="说明依据"):
        prepare(data["protocol"], data["questions"], data["scopes"], data["triage"])


def test_scope_missing_and_duplicate_cases(bundle):
    data, *_ = bundle
    with pytest.raises(ValueError, match="授权"):
        prepare(data["protocol"], data["questions"], [], data["triage"])
    data["questions"].append(data["questions"][0])
    with pytest.raises(ValueError, match="重复"):
        prepare(data["protocol"], data["questions"], data["scopes"], data["triage"])


def test_review_excludes_ranks_and_includes_full_catalog(bundle):
    _, pool, *_ = bundle
    packet = review_packet(pool, "reviewer-1", "human")
    assert len(packet["cases"][0]["catalog"]) == 30
    assert "results" not in packet["cases"][0]
    assert "prepared" not in packet


def test_real_questions_require_human_review(bundle):
    _, pool, a, _ = bundle
    payload = unseal(pool, "pool")
    payload["prepared"]["protocol"]["source_kind"] = "pilot"
    pool = seal("pool", payload)
    a["pool_sha256"] = pool["sha256"]
    with pytest.raises(ValueError, match="人工"):
        validate_review(pool, a)
    with pytest.raises(ValueError, match="人工"):
        review_packet(pool, "fixture-A", "scripted_fixture")


def test_reviewer_independence_and_exhaustive_labels(bundle):
    _, pool, a, b = bundle
    with pytest.raises(ValueError, match="不同评审者"):
        reconcile(pool, a, a)
    b["labels"].pop()
    with pytest.raises(ValueError, match="覆盖"):
        reconcile(pool, a, b)
    a["independent_review_declared"] = False
    with pytest.raises(ValueError, match="独立"):
        validate_review(pool, a)


def test_unauthorized_label_is_rejected(bundle):
    _, pool, a, _ = bundle
    a["labels"][0]["relevant_ids"] = ["hidden_private"]
    with pytest.raises(ValueError, match="授权"):
        validate_review(pool, a)


def test_conflicts_require_distinct_adjudicator(bundle):
    _, pool, a, b = bundle
    b["labels"][0].update(category="needs_context", relevant_ids=[])
    reconciled = reconcile(pool, a, b)
    with pytest.raises(ValueError, match="裁决"):
        freeze(pool, reconciled)
    adjudicated = {**a, "labels": [a["labels"][0]]}
    with pytest.raises(ValueError, match="第三位"):
        freeze(pool, reconciled, adjudicated)
    adjudicated["reviewer_id"] = "fixture-C"
    assert len(freeze(pool, reconciled, adjudicated)["payload"]["cases"]) == 80


def test_split_is_deterministic_and_prevents_leakage(bundle):
    data, pool, *_ = bundle
    cases = pool["payload"]["prepared"]["cases"]
    protocol = Protocol.model_validate(data["protocol"])
    assigned = assign_splits(cases, protocol)
    assert assigned == assign_splits(cases, protocol)
    for key in ("participant_id", "family_id", "retrieval_query"):
        owners = {}
        for row in cases:
            assert owners.setdefault(row[key], assigned[row["case_id"]]) == assigned[row["case_id"]]
    for row in cases:
        row["participant_id"] = "one-user"
    with pytest.raises(ValueError, match="两个"):
        assign_splits(cases, protocol)


def test_artifact_tamper_and_no_overwrite(tmp_path):
    path = tmp_path / "evidence.json"
    artifact = seal("prepared", {"one": 1})
    save(path, artifact)
    assert read(path) == artifact
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save(path, artifact)
    artifact["payload"]["one"] = 2
    with pytest.raises(ValueError, match="摘要"):
        unseal(artifact, "prepared")


async def test_runtime_failures_are_retained_and_snapshot_changes_abort(bundle):
    _, pool, a, b = bundle
    frozen = freeze(pool, reconcile(pool, a, b))
    runtime = object.__new__(StudyRuntime)
    runtime.retrieve = AsyncMock(side_effect=RuntimeError("provider secret"))
    runtime.provenance = dict
    result = await runtime.evaluate(frozen, "test")
    summary = summarize(result)
    assert summary["failure_count"] == summary["case_count"]
    assert summary["methods"]["vector"]["recall"] == 0
    assert "provider secret" not in str(result)
    runtime.retrieve = AsyncMock(side_effect=SnapshotChanged("changed"))
    with pytest.raises(SnapshotChanged):
        await runtime.evaluate(frozen, "test")


def test_report_denominators_difference_and_privacy():
    rows = []
    for i, category in enumerate(["answerable", "answerable", "no_answer", "business_tool"]):
        rows.append(
            {
                "case": {"query": "PRIVATE-QUESTION", "participant_id": "PRIVATE-USER"},
                "label": {
                    "category": category,
                    "relevant_ids": ["a"] if category == "answerable" else [],
                },
                "group_id": str(i),
                "results": {
                    method: (["a"] if method != "vector" or i else ["b"]) for method in METHODS
                },
                "error": None,
            }
        )
    report = summarize(
        seal(
            "run",
            {
                "protocol": generate("report-test")["protocol"],
                "reviewer_kinds": ["scripted_fixture"],
                "cases": rows,
                "runtime": {},
                "split": "test",
                "frozen_sha256": "abc",
                "created_at": "now",
            },
        )
    )
    assert report["answerable_count"] == 2
    assert report["methods"]["vector"]["recall"] == 0.5
    assert report["comparison"]["recall_delta_percentage_points"] == 50
    assert report["comparison"]["recall_relative_change_percent"] == 100
    assert report["comparison"]["stable_positive_difference_supported"] is False
    assert "PRIVATE-" not in str(report)
    assert "历史生产" in markdown(report)


def test_catalog_limit_and_authorization_sql():
    # 结构回归加真实demo的四种不可访问文档；不能仅靠这个测试宣称全平台安全。
    source = (Path(__file__).parents[1] / "app/rag/study/runtime.py").read_text()
    for predicate in (
        "c.visibility=d.visibility",
        "d.status='PUBLISHED'",
        "c.owner_user_id=:subject",
        "c.organization_id IN :organizations",
        "c.allowed_roles",
        "LIMIT 10001",
        "REPEATABLE READ, READ ONLY",
        "statement_timeout",
    ):
        assert predicate in source


async def test_pool_uses_each_case_authorization(bundle):
    _, pool, *_ = bundle
    prepared = pool["payload"]["prepared"]
    runtime = object.__new__(StudyRuntime)
    runtime.retrieve = AsyncMock(
        return_value={
            "catalog": [],
            "catalog_sha256": digest([]),
            "candidate_ids": [],
            "results": {},
        }
    )
    runtime.provenance = dict
    result = await runtime.build_pool(seal("prepared", prepared))
    assert len(result["payload"]["cases"]) == 80
    for call, row in zip(runtime.retrieve.call_args_list, prepared["cases"], strict=True):
        assert call.args[1] == prepared["scopes"][row["participant_id"]]


def test_import_creates_unverified_drafts_and_rejects_source_mixing(tmp_path):
    from scripts.rag_study import import_questions, initialize

    directory = tmp_path / "pilot-study"
    initialize(directory, "pilot", directory.name)
    records = generate("input-study")["questions"][:2]
    source = tmp_path / "synthetic.json"
    save(source, records)
    with pytest.raises(ValueError, match="来源"):
        import_questions(directory, [source])
    # 仅测试来源契约，不表示这两条模拟记录变成了真实来源；不对外输出这份fixture。
    for row in records:
        row["source_kind"] = "pilot"
    pilot_fixture = tmp_path / "pilot-contract-fixture.json"
    save(pilot_fixture, records)
    import_questions(directory, [pilot_fixture])
    assert all(not row["source_reviewed"] for row in read(directory / "triage-draft.json"))
    assert all(not row["roles"] for row in read(directory / "scopes-draft.json"))
    with pytest.raises(ValueError):
        prepare(
            read(directory / "protocol.json"),
            read(directory / "questions-imported.json"),
            read(directory / "scopes-draft.json"),
            read(directory / "triage-draft.json"),
        )


def test_scope_blank_identifiers_are_not_valid_authorization(bundle):
    data, *_ = bundle
    data["scopes"][0]["organization_ids"] = [" "]
    with pytest.raises(ValueError, match="空白"):
        prepare(data["protocol"], data["questions"], data["scopes"], data["triage"])
