from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.tool_registry import ToolRegistry
from app.evaluation.task_eval import routing_role_boundary_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_role_and_registry_observations_do_not_claim_task_pass():
    dataset = load_dataset(DATASET)
    dev = await probes.evaluate_routing_role_boundaries(dataset, split="dev")
    test = await probes.evaluate_routing_role_boundaries(dataset, split="test")
    rows = {r["case_id"]: r for r in dev["cases"] + test["cases"]}
    assert set(rows) == {"ROUT-019", "ROUT-020"}
    assert all(
        r["component_status"] == "PASS" and r["task_status"] == "UNKNOWN" for r in rows.values()
    )
    assert rows["ROUT-019"]["observed"]["outcome"] == "ROLE_FORBIDDEN"
    assert rows["ROUT-019"]["observed"]["synthetic_gateway_calls"] == 0
    assert rows["ROUT-020"]["observed"]["outcome"] == "UNKNOWN_TOOL"
    assert rows["ROUT-020"]["observed"]["registered_contract_write_count"] == 0


@pytest.mark.asyncio
async def test_role_guard_bypass_is_not_confused_with_authorization(monkeypatch):
    original = probes.build_operations_tool_definitions

    def relaxed(*args, **kwargs):
        return tuple(
            replace(d, allowed_roles=d.allowed_roles | {"STUDENT"})
            for d in original(*args, **kwargs)
        )

    monkeypatch.setattr(probes, "build_operations_tool_definitions", relaxed)
    report = await probes.evaluate_routing_role_boundaries(load_dataset(DATASET), split="dev")
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["observed"]["outcome"] != "ROLE_FORBIDDEN"
    assert report["cases"][0]["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_unknown_tool_redirect_is_detected(monkeypatch):
    original = ToolRegistry.get

    def unsafe(self, tool_id):
        return original(
            self, "fitness.contract.list.v1" if tool_id == probes._GUESSED_WRITE else tool_id
        )

    monkeypatch.setattr(ToolRegistry, "get", unsafe)
    report = await probes.evaluate_routing_role_boundaries(load_dataset(DATASET), split="test")
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_drifted_case_is_unknown_and_never_executes(monkeypatch):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-020")
    changed = case.model_copy(update={"actor": case.actor.model_copy(update={"role": "COACH"})})

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")

    monkeypatch.setattr(probes, "_run", forbidden)
    report = await probes.evaluate_routing_role_boundaries(
        replace(dataset, cases=[changed]), split="test"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"
