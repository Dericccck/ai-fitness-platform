from dataclasses import replace
from pathlib import Path

import pytest

import app.agent.supervisor as supervisor_module
from app.agent.tool_registry import ToolRegistry
from app.evaluation.task_eval import customer_service_boundary_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_customer_service_guards_are_only_component_observations():
    dataset = load_dataset(DATASET)
    dev = await probes.evaluate_customer_service_boundaries(dataset, split="dev")
    test = await probes.evaluate_customer_service_boundaries(dataset, split="test")
    rows = {r["case_id"]: r for r in dev["cases"] + test["cases"]}
    assert set(rows) == {"OPER-017", "OPER-018", "OPER-020"}
    assert all(
        r["component_status"] == "PASS" and r["task_status"] == "UNKNOWN" for r in rows.values()
    )
    assert rows["OPER-017"]["observed"]["model_calls"] == 0
    assert rows["OPER-018"]["observed"]["block"] == "INTENT_TOOL_ALLOWLIST"
    assert rows["OPER-020"]["observed"]["synthetic_gateway_reads"] == [
        {"organization_id": "org_a", "user_id": "actor_a", "limit": 20}
    ]
    assert not rows["OPER-020"]["observed"]["foreign_marker_seen_by_model"]
    assert probes._FOREIGN_MARKER not in str(dev) + str(test)


@pytest.mark.asyncio
async def test_context_binding_bypass_is_detected(monkeypatch):
    monkeypatch.setattr(
        ToolRegistry,
        "bind_context_input",
        lambda self, tool_id, raw_input, identity: dict(raw_input),
    )
    report = await probes.evaluate_customer_service_boundaries(load_dataset(DATASET), split="test")
    row = report["cases"][0]
    assert row["component_status"] == "FAIL"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["foreign_marker_seen_by_model"]


@pytest.mark.asyncio
async def test_ticket_intent_guard_bypass_is_not_accepted(monkeypatch):
    original = supervisor_module.intent_allowed_tool_ids

    def relaxed(route, intent):
        allowed = original(route, intent)
        return allowed | {"fitness.support.ticket.create.v1"} if allowed is not None else None

    monkeypatch.setattr(supervisor_module, "intent_allowed_tool_ids", relaxed)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-018")
    report = await probes.evaluate_customer_service_boundaries(
        replace(dataset, cases=[case]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_case_drift_stops_observation(monkeypatch):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-020")
    changed = case.model_copy(update={"actor": case.actor.model_copy(update={"role": "ADMIN"})})

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")

    monkeypatch.setattr(probes, "_run", forbidden)
    report = await probes.evaluate_customer_service_boundaries(
        replace(dataset, cases=[changed]), split="test"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"


@pytest.mark.asyncio
async def test_refund_hard_guard_bypass_is_detected(monkeypatch):
    monkeypatch.setattr(supervisor_module, "_customer_service_restricted_answer", lambda _: None)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-017")
    report = await probes.evaluate_customer_service_boundaries(
        replace(dataset, cases=[case]), split="dev"
    )
    row = report["cases"][0]
    assert row["component_status"] == "FAIL"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["model_calls"] == 1
