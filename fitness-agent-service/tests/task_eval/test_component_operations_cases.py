from dataclasses import replace
from pathlib import Path

import pytest

from app.agent import operations_tools
from app.agent.operations_tools import OperationsQueryPolicyDecision
from app.evaluation.task_eval import component_operations_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_case_bound_probes_never_claim_end_to_end_success():
    dataset = load_dataset(DATASET)
    rows = []
    for split in ("dev", "test"):
        rows.extend(
            (await probes.evaluate_operations_case_observations(dataset, split=split))["cases"]
        )
    assert {r["case_id"] for r in rows} == {*(f"OPER-{i:03d}" for i in range(1, 9)), "SAFE-003"}
    assert all(r["component_status"] == "PASS" and r["task_status"] == "UNKNOWN" for r in rows)


@pytest.mark.asyncio
async def test_changed_case_contract_fails_closed():
    dataset = load_dataset(DATASET)
    original = next(c for c in dataset.cases if c.case_id == "OPER-001")
    altered_step = original.steps[0].model_copy(update={"message": "查询本月营收"})
    altered = original.model_copy(update={"steps": [altered_step, *original.steps[1:]]})
    dataset = replace(dataset, cases=[altered])
    report = await probes.evaluate_operations_case_observations(dataset, split=original.split)
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"


@pytest.mark.asyncio
async def test_probe_detects_policy_bypass(monkeypatch):
    def allow(*args, **kwargs):
        return OperationsQueryPolicyDecision(True, "ALLOWED", "test mutation")

    monkeypatch.setattr(operations_tools, "validate_operations_query_policy", allow)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-001")
    report = await probes.evaluate_operations_case_observations(
        replace(dataset, cases=[case]), split=case.split
    )
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["observed"]["gateway_calls"] == 1


@pytest.mark.asyncio
async def test_probe_detects_missing_role_guard(monkeypatch):
    original = probes.build_operations_tool_definitions

    def allow_student(*args, **kwargs):
        return tuple(
            replace(d, allowed_roles=d.allowed_roles | {"STUDENT"})
            for d in original(*args, **kwargs)
        )

    monkeypatch.setattr(probes, "build_operations_tool_definitions", allow_student)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "SAFE-003")
    report = await probes.evaluate_operations_case_observations(
        replace(dataset, cases=[case]), split=case.split
    )
    assert report["cases"][0]["component_status"] == "FAIL"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", [f"OPER-{i:03d}" for i in (2, 3, 4, 6, 8)])
async def test_additional_case_probe_detects_policy_bypass(case_id, monkeypatch):
    monkeypatch.setattr(
        operations_tools,
        "validate_operations_query_policy",
        lambda *args, **kwargs: OperationsQueryPolicyDecision(True, "ALLOWED", "mutation"),
    )
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == case_id)
    report = await probes.evaluate_operations_case_observations(
        replace(dataset, cases=[case]), split=case.split
    )
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["observed"]["gateway_calls"] == 1


@pytest.mark.asyncio
async def test_store_probe_detects_identity_binding_bypass(monkeypatch):
    monkeypatch.setattr(
        probes.ToolRegistry,
        "bind_context_input",
        lambda self, tool_id, raw_input, identity: dict(raw_input),
    )
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-005")
    report = await probes.evaluate_operations_case_observations(
        replace(dataset, cases=[case]), split=case.split
    )
    assert report["cases"][0]["component_status"] == "FAIL"
    assert report["cases"][0]["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", [f"OPER-{i:03d}" for i in range(2, 9)])
async def test_additional_cases_refuse_actor_or_expected_drift(case_id):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == case_id)
    actor = case.model_copy(deep=True)
    actor.actor.user_ref = "other"
    expected = case.model_copy(deep=True)
    expected.expected.assertions[0].parameters["reference_points"] = ["不同口径"]
    for altered in (actor, expected):
        report = await probes.evaluate_operations_case_observations(
            replace(dataset, cases=[altered]), split=case.split
        )
        assert report["cases"][0]["component_status"] == "UNKNOWN"
        assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"
