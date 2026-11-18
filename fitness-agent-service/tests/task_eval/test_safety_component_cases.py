from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.tool_registry import ToolRegistry
from app.evaluation.task_eval import safety_component_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_case_bound_probes_are_components_only():
    dataset = load_dataset(DATASET)
    rows = []
    for split in ("dev", "test"):
        rows.extend((await probes.evaluate_safety_case_observations(dataset, split=split))["cases"])
    assert {row["case_id"] for row in rows} == {
        "SAFE-001",
        "SAFE-002",
        "SAFE-004",
        "SAFE-006",
        "SAFE-007",
        "SAFE-008",
        "SAFE-009",
        "SAFE-012",
    }
    assert all(
        row["component_status"] == "PASS" and row["task_status"] == "UNKNOWN" for row in rows
    )


@pytest.mark.asyncio
async def test_case_changes_fail_closed():
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == "SAFE-006")
    changed_step = case.steps[0].model_copy(update={"message": "其他请求"})
    changed = case.model_copy(update={"steps": [changed_step, case.steps[1]]})
    result = await probes.evaluate_safety_case_observations(
        replace(dataset, cases=[changed]), split="dev"
    )
    assert result["cases"][0]["component_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_identity_binding_bypass_is_detected(monkeypatch):
    monkeypatch.setattr(
        ToolRegistry, "bind_context_input", lambda self, tool_id, raw, identity: dict(raw)
    )
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == "SAFE-001")
    result = await probes.evaluate_safety_case_observations(
        replace(dataset, cases=[case]), split="dev"
    )
    assert result["cases"][0]["component_status"] == "FAIL"
    assert result["cases"][0]["observed"]["gateway_reads"] == [
        {"organization_id": "org_b", "user_id": "other"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "split", "kind"),
    [
        ("SAFE-006", "dev", "create"),
        ("SAFE-007", "dev", "cancel"),
        ("SAFE-008", "dev", "reschedule"),
        ("SAFE-004", "test", "cancel"),
        ("SAFE-012", "test", "create"),
    ],
)
async def test_confirmation_gate_bypass_is_detected(monkeypatch, case_id, split, kind):
    original = ToolRegistry.invoke

    async def fake_invoke(self, tool_id, raw, context):
        forged = replace(context.gateway_context, confirmation_token="forged")
        return await original(self, tool_id, raw, replace(context, gateway_context=forged))

    monkeypatch.setattr(ToolRegistry, "invoke", fake_invoke)
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == case_id)
    result = await probes.evaluate_safety_case_observations(
        replace(dataset, cases=[case]), split=split
    )
    assert result["cases"][0]["component_status"] == "FAIL"
    assert result["cases"][0]["observed"]["gateway_writes"] == [kind]


@pytest.mark.asyncio
async def test_rejection_probe_detects_improper_approval(monkeypatch):
    original = probes.booking_local.Repository.decide

    async def wrong_decision(
        self, cid, subject, orgs, decision, decision_id, now, trace, actor, roles
    ):
        return await original(
            self, cid, subject, orgs, "APPROVE", decision_id, now, trace, actor, roles
        )

    monkeypatch.setattr(probes.booking_local.Repository, "decide", wrong_decision)
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == "SAFE-009")
    result = await probes.evaluate_safety_case_observations(
        replace(dataset, cases=[case]), split=case.split
    )
    assert result["cases"][0]["component_status"] == "FAIL"
    assert result["cases"][0]["task_status"] == "UNKNOWN"
    assert result["cases"][0]["observed"]["checks"]["rejected"] is False


@pytest.mark.asyncio
async def test_rejection_probe_case_contract_drift_is_not_reused():
    dataset = load_dataset(DATASET)
    case = next(case for case in dataset.cases if case.case_id == "SAFE-009")
    for attr in ("actor", "expected", "fixture"):
        changed = case.model_copy(deep=True)
        if attr == "actor":
            changed.actor.user_ref = "different"
        elif attr == "expected":
            changed.expected.assertions[0].parameters["reference_points"] = ["different"]
        else:
            changed.fixture.namespace_isolated = False
        result = await probes.evaluate_safety_case_observations(
            replace(dataset, cases=[changed]), split=case.split
        )
        assert result["cases"][0]["component_status"] == "UNKNOWN"
        assert result["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"
