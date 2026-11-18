from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.tool_registry import ToolRegistry
from app.evaluation.task_eval import routing_remaining_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_observations_are_case_bound_and_not_task_passes():
    dataset = load_dataset(DATASET)
    dev = await probes.evaluate_remaining_routing_observations(dataset, split="dev")
    test = await probes.evaluate_remaining_routing_observations(dataset, split="test")
    rows = {r["case_id"]: r for r in dev["cases"] + test["cases"]}
    assert set(rows) == {"ROUT-005", "ROUT-007", "ROUT-014"}
    assert all(
        r["component_status"] == "PASS" and r["task_status"] == "UNKNOWN" for r in rows.values()
    )
    assert rows["ROUT-005"]["observed"]["router_stub_calls"] == 1
    assert rows["ROUT-005"]["observed"]["domain_stub_calls"] == 0
    assert rows["ROUT-007"]["observed"]["metric_outcome"] == "ROLE_FORBIDDEN"
    assert not rows["ROUT-007"]["observed"]["synthetic_foreign_marker_returned"]
    assert rows["ROUT-014"]["observed"]["intent"] == "QUERY_CONTRACT"
    assert not rows["ROUT-014"]["observed"]["write_tools"]


@pytest.mark.asyncio
async def test_subject_binding_bypass_is_detected(monkeypatch):
    monkeypatch.setattr(
        ToolRegistry,
        "bind_context_input",
        lambda self, tool_id, raw_input, identity: dict(raw_input),
    )
    report = await probes.evaluate_remaining_routing_observations(
        load_dataset(DATASET), split="test"
    )
    row = next(r for r in report["cases"] if r["case_id"] == "ROUT-007")
    assert row["component_status"] == "FAIL"
    assert row["observed"]["synthetic_foreign_marker_returned"]
    assert row["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_relaxed_metric_role_is_detected(monkeypatch):
    original = probes.build_operations_tool_definitions

    def relaxed(*args, **kwargs):
        return tuple(
            replace(d, allowed_roles=d.allowed_roles | {"STUDENT"})
            for d in original(*args, **kwargs)
        )

    monkeypatch.setattr(probes, "build_operations_tool_definitions", relaxed)
    report = await probes.evaluate_remaining_routing_observations(
        load_dataset(DATASET), split="test"
    )
    row = next(r for r in report["cases"] if r["case_id"] == "ROUT-007")
    assert row["component_status"] == "FAIL"
    assert row["observed"]["metric_outcome"] != "ROLE_FORBIDDEN"


def test_unrequested_write_in_allowlist_is_detected(monkeypatch):
    original = probes.intent_allowed_tool_ids

    def unsafe(route, intent):
        allowed = original(route, intent)
        return allowed | {"fitness.memory.save.v1"} if allowed is not None else None

    monkeypatch.setattr(probes, "intent_allowed_tool_ids", unsafe)
    assert probes._neutral_route("我的合同有问题")["component_status"] == "FAIL"


@pytest.mark.asyncio
async def test_case_drift_is_unknown_before_execution(monkeypatch):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-007")
    changed = case.model_copy(update={"actor": case.actor.model_copy(update={"role": "ADMIN"})})

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")

    monkeypatch.setattr(probes, "_role_probe", forbidden)
    report = await probes.evaluate_remaining_routing_observations(
        replace(dataset, cases=[changed]), split="test"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"
