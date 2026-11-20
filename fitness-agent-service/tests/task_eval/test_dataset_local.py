import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import dataset_local
from app.evaluation.task_eval.dataset import load_dataset


@pytest.fixture
def dataset():
    path = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
    return load_dataset(path)


@pytest.mark.asyncio
async def test_selected_split_only_and_no_component_becomes_task_pass(dataset, monkeypatch):
    observed = []

    async def retrieval(current):
        observed.extend(c.case_id for c in current.cases)
        return {"cases": []}

    async def notifications():
        return {"cases": []}

    async def extended(current):
        assert all(c.split == "dev" for c in current.cases)
        return {"cases": []}

    monkeypatch.setattr(dataset_local, "evaluate_knowledge_dataset", retrieval)
    monkeypatch.setattr(dataset_local, "evaluate_notification_worker", notifications)
    monkeypatch.setattr(dataset_local, "evaluate_knowledge_extended", extended)
    monkeypatch.setattr(dataset_local.booking_cases, "supports", lambda _: False)
    result = await dataset_local.evaluate_dataset(dataset, split="dev")
    assert len(observed) == 80
    assert all(c.split == "dev" for c in dataset.cases if c.case_id in observed)
    assert result["summary"]["planned_cases"] == 80
    assert "PASS" not in result["summary"]["task_status_counts"]
    service_root = Path(__file__).resolve().parents[2]
    fixture = "evals/task_eval/dev_fixtures/book-018-v1.json"
    assert (
        result["manifest"]["known_fixture_sha256"][fixture]
        == hashlib.sha256((service_root / fixture).read_bytes()).hexdigest()
    )
    assert "evaluation/task_eval/booking_dev_fixtures.py" in result["manifest"]["source_sha256"]
    fixture = "evals/task_eval/dev_fixtures/book-026-v1.json"
    assert (
        result["manifest"]["known_fixture_sha256"][fixture]
        == hashlib.sha256((service_root / fixture).read_bytes()).hexdigest()
    )
    assert "evaluation/task_eval/booking_dev_026.py" in result["manifest"]["source_sha256"]
    assert result["manifest"]["fixture_sha256"] == "unknown"


@pytest.mark.asyncio
async def test_out_of_scope_and_adapter_error_preserve_planned_counts(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.primary_scenario == "knowledge")
    other = case.model_copy(update={"case_id": "out", "delivery_scope": "out_of_scope"})

    async def retrieval(_):
        raise RuntimeError("private details")

    async def notifications():
        return {"cases": []}

    async def extended(_):
        return {"cases": []}

    monkeypatch.setattr(dataset_local, "evaluate_knowledge_dataset", retrieval)
    monkeypatch.setattr(dataset_local, "evaluate_notification_worker", notifications)
    monkeypatch.setattr(dataset_local, "evaluate_knowledge_extended", extended)
    result = await dataset_local.evaluate_dataset(
        replace(dataset, cases=[case, other]), split="dev"
    )
    assert result["summary"]["planned_cases"] == 1
    assert result["summary"]["out_of_scope_cases"] == 1
    assert result["summary"]["task_status_counts"] == {"ERROR": 1}
    assert result["cases"][1]["reason"] == "OUT_OF_SCOPE"
    assert "private details" not in str(result)


@pytest.mark.asyncio
async def test_notification_fixture_is_not_reused_after_case_drift(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "OPER-009")
    altered = case.model_copy(deep=True)
    altered.expected.reference_points = ["a different scenario"]

    async def knowledge(_):
        return {"cases": []}

    async def notifications():
        return {"cases": [{"case_id": "OPER-009", "component_status": "PASS"}]}

    monkeypatch.setattr(dataset_local, "evaluate_knowledge_dataset", knowledge)
    monkeypatch.setattr(dataset_local, "evaluate_knowledge_extended", knowledge)
    monkeypatch.setattr(dataset_local, "evaluate_notification_worker", notifications)
    report = await dataset_local.evaluate_dataset(
        replace(dataset, cases=[altered]), split=case.split
    )
    assert report["cases"][0]["task_status"] == "ERROR"
    assert "DRAFT_OUTREACH_COMPONENT_ERROR" in report["cases"][0]["reasons"]
    assert "notification_worker" not in report["cases"][0]["observations"]
    assert "draft_outreach" not in report["cases"][0]["observations"]


@pytest.mark.asyncio
async def test_notification_fixture_is_not_reused_after_actor_drift(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "OPER-009").model_copy(deep=True)
    case.actor.user_ref = "different_actor"

    async def notifications():
        return {"cases": [{"case_id": "OPER-009", "component_status": "PASS"}]}

    monkeypatch.setattr(dataset_local, "evaluate_notification_worker", notifications)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["cases"][0]["task_status"] == "ERROR"
    assert "DRAFT_OUTREACH_COMPONENT_ERROR" in report["cases"][0]["reasons"]
    assert "notification_worker" not in report["cases"][0]["observations"]
    assert "draft_outreach" not in report["cases"][0]["observations"]


@pytest.mark.asyncio
async def test_component_error_survives_later_observation(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "KNOW-021")

    async def failed_retrieval(_):
        raise RuntimeError("private error")

    async def observed(_):
        return {"cases": [{"case_id": "KNOW-021", "status": "OBSERVED"}]}

    async def notifications():
        return {"cases": []}

    monkeypatch.setattr(dataset_local, "evaluate_knowledge_dataset", failed_retrieval)
    monkeypatch.setattr(dataset_local, "evaluate_knowledge_extended", observed)
    monkeypatch.setattr(dataset_local, "evaluate_notification_worker", notifications)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["cases"][0]["task_status"] == "ERROR"
    assert report["cases"][0]["reason"] == "RETRIEVAL_ADAPTER_ERROR"
    assert "TRAINING_OR_MOVEMENT_COMPONENT_ONLY" in report["cases"][0]["reasons"]


@pytest.mark.asyncio
async def test_customer_service_failure_is_counted_without_claiming_task_pass(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "OPER-019")

    def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_customer_service_case_observations", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"
    assert (
        report["cases"][0]["observations"]["customer_service_route"]["component_status"] == "FAIL"
    )


@pytest.mark.asyncio
async def test_customer_service_probe_error_is_reported_without_disclosing_details(
    dataset, monkeypatch
):
    case = next(c for c in dataset.cases if c.case_id == "OPER-019")

    def broken_probe(_, *, split):
        raise RuntimeError("private details")

    monkeypatch.setattr(dataset_local, "evaluate_customer_service_case_observations", broken_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["cases"][0]["task_status"] == "ERROR"
    assert "private details" not in str(report)


@pytest.mark.asyncio
async def test_restricted_safety_failure_is_counted(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "SAFE-015")

    def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_safety_restricted_observations", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_intent_component_failure_is_counted_without_claiming_task_failure(
    dataset, monkeypatch
):
    case = next(c for c in dataset.cases if c.case_id == "ROUT-015")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_intent_observations", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"
    assert report["cases"][0]["observations"]["intent_routing"]["component_status"] == "FAIL"


@pytest.mark.asyncio
async def test_injection_component_failure_is_counted_without_claiming_task_failure(
    dataset, monkeypatch
):
    case = next(c for c in dataset.cases if c.case_id == "SAFE-010")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_injection_observations", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"
    assert report["cases"][0]["observations"]["injection_guard"]["component_status"] == "FAIL"


@pytest.mark.asyncio
async def test_service_boundary_failure_is_counted_without_claiming_task_failure(
    dataset, monkeypatch
):
    case = next(c for c in dataset.cases if c.case_id == "OPER-017")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_customer_service_boundaries", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"
    assert (
        report["cases"][0]["observations"]["customer_service_boundary"]["component_status"]
        == "FAIL"
    )


@pytest.mark.asyncio
async def test_routing_role_failure_is_counted_without_claiming_task_failure(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "ROUT-019")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_routing_role_boundaries", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_clarification_failure_is_counted_without_claiming_task_failure(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "ROUT-009")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(
        dataset_local, "evaluate_supervisor_clarification_observations", failing_probe
    )
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_remaining_routing_failure_is_counted_without_claiming_task_failure(
    dataset, monkeypatch
):
    case = next(c for c in dataset.cases if c.case_id == "ROUT-014")

    async def failing_probe(_, *, split):
        return {"cases": [{"case_id": case.case_id, "component_status": "FAIL"}]}

    monkeypatch.setattr(dataset_local, "evaluate_remaining_routing_observations", failing_probe)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split=case.split)
    assert report["summary"]["component_failures"] == 1
    assert report["cases"][0]["task_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_draft_knowledge_integrated_only_for_selected_split(dataset, monkeypatch):
    monkeypatch.setattr(dataset_local.booking_cases, "supports", lambda _: False)
    report = await dataset_local.evaluate_dataset(dataset, split="test")
    rows = {row["case_id"]: row for row in report["cases"]}
    for cid in ("KNOW-008", "KNOW-009", "KNOW-010"):
        row = rows[cid]
        component = row["observations"]["draft_knowledge_retrieval"]
        assert component["retrieval"]["status"] == "PASS"
        assert component["task_status"] == "UNKNOWN"
        assert component["status"] == "draft_unreviewed_not_release_evidence"
        assert row["task_status"] == "INCONCLUSIVE"
    assert "KNOW-001" not in rows
    assert (
        report["manifest"]["known_fixture_sha256"][
            "evals/task_eval/draft_knowledge_fixture_v1/corpus.json"
        ]
        == dataset_local.CORPUS_SHA256
    )


@pytest.mark.asyncio
async def test_drifted_dataset_never_reuses_draft_knowledge_labels(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "KNOW-001").model_copy(deep=True)
    case.steps[0].message = "changed question"
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split="dev")
    row = report["cases"][0]
    assert row["task_status"] == "ERROR"
    assert "DRAFT_KNOWLEDGE_COMPONENT_ERROR" in row["reasons"]
    assert "draft_knowledge_retrieval" not in row["observations"]


@pytest.mark.asyncio
async def test_draft_outreach_executes_selected_split_without_task_promotion(dataset, monkeypatch):
    seen = []
    original = dataset_local.evaluate_draft_outreach

    async def observe(current, *, case_ids):
        seen.append(case_ids)
        return await original(current, case_ids=case_ids)

    monkeypatch.setattr(dataset_local, "evaluate_draft_outreach", observe)
    monkeypatch.setattr(dataset_local.booking_cases, "supports", lambda _: False)
    report = await dataset_local.evaluate_dataset(dataset, split="dev")
    assert seen == [frozenset(f"OPER-{i:03d}" for i in range(9, 14))]
    rows = {row["case_id"]: row for row in report["cases"]}
    for cid in seen[0]:
        item = rows[cid]["observations"]["draft_outreach"]
        assert item["component_grade"]["status"] == "PASS"
        assert item["task_status"] == "UNKNOWN"
        assert item["status"] == "draft_unreviewed_not_release_evidence"
        assert rows[cid]["task_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_draft_outreach_component_failure_is_counted(dataset, monkeypatch):
    async def failed(_dataset, *, case_ids):
        return {
            "fixture_sha256": "test-only",
            "cases": [
                {
                    "case_id": cid,
                    "component_grade": {"status": "FAIL"},
                    "task_status": "UNKNOWN",
                }
                for cid in case_ids
            ],
        }

    monkeypatch.setattr(dataset_local, "evaluate_draft_outreach", failed)
    case = next(c for c in dataset.cases if c.case_id == "OPER-014")
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]), split="test")
    row = report["cases"][0]
    assert row["task_status"] == "INCONCLUSIVE"
    assert row["observations"]["draft_outreach"]["component_grade"]["status"] == "FAIL"
    assert report["summary"]["component_failures"] >= 1


@pytest.mark.asyncio
async def test_draft_knowledge_missing_component_result_is_error(dataset, monkeypatch):
    async def missing(_dataset, *, case_ids):
        assert "KNOW-001" in case_ids
        return {"cases": []}

    monkeypatch.setattr(dataset_local, "evaluate_draft_corpus", missing)
    monkeypatch.setattr(dataset_local.booking_cases, "supports", lambda _: False)
    report = await dataset_local.evaluate_dataset(dataset, split="dev")
    row = next(row for row in report["cases"] if row["case_id"] == "KNOW-001")
    assert row["task_status"] == "ERROR"
    assert "DRAFT_KNOWLEDGE_COMPONENT_ERROR" in row["reasons"]
    assert "draft_knowledge_retrieval" not in row["observations"]
