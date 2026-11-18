from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.routing import RouteDecision, RoutedIntent
from app.evaluation.task_eval import routing_intent_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def decision(*intents, clarification=None):
    return RouteDecision(
        intents=tuple(intents),
        source="DETERMINISTIC_RULE",
        confidence=1,
        reason_code="TEST",
        classifier_version="test",
        clarification=clarification,
    )


@pytest.mark.asyncio
async def test_real_routing_observations_preserve_task_unknown():
    dataset = load_dataset(DATASET)
    for split, expected_count in (("dev", 8), ("test", 4)):
        report = await probes.evaluate_intent_observations(dataset, split=split)
        assert len(report["cases"]) == expected_count
        assert all(row["task_status"] == "UNKNOWN" for row in report["cases"])
        assert all(row["observed"]["network_calls"] == 0 for row in report["cases"])
    rows = {
        row["case_id"]: row
        for row in (await probes.evaluate_intent_observations(dataset, split="dev"))["cases"]
    }
    assert rows["ROUT-012"]["component_status"] == "PASS"
    assert rows["ROUT-015"]["component_status"] == "PASS"
    assert rows["ROUT-016"]["component_status"] == "PASS"
    assert rows["ROUT-019"]["component_status"] == "UNKNOWN"
    assert rows["ROUT-019"]["reason"] == "REQUIRES_DOWNSTREAM_OR_SEMANTIC_EVIDENCE"


@pytest.mark.asyncio
async def test_drifted_input_actor_reference_assertion_or_split_is_unknown():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-015")
    changes = [
        {"steps": [case.steps[0].model_copy(update={"message": "changed"}), case.steps[1]]},
        {"actor": case.actor.model_copy(update={"role": "ADMIN"})},
        {"expected": case.expected.model_copy(update={"reference_points": ["changed"]})},
        {"expected": case.expected.model_copy(update={"allowed_routes": ["BOOKING"]})},
        {"split": "test"},
    ]
    for change in changes:
        changed = case.model_copy(update=change)
        report = await probes.evaluate_intent_observations(
            replace(dataset, cases=[changed]), split="dev"
        )
        row = report["cases"][0]
        assert row["reason"] == "CASE_CONTRACT_CHANGED"
        assert row["component_status"] == row["task_status"] == "UNKNOWN"
        assert "observed" not in row


def test_multi_intent_order_and_clarification_counterexamples():
    q = RoutedIntent("FITNESS_COACHING", "FITNESS_KNOWLEDGE_QA")
    b = RoutedIntent("BOOKING", "CREATE_BOOKING")
    assert probes._component_assessment("ROUT-015", decision(q, b))[0] == "PASS"
    assert probes._component_assessment("ROUT-015", decision(b, q))[0] == "FAIL"
    assert probes._component_assessment("ROUT-015", decision(q))[0] == "FAIL"
    assert (
        probes._component_assessment("ROUT-015", decision(clarification="请说明"))[0] == "UNKNOWN"
    )
    assert probes._component_assessment("ROUT-012", decision(q))[0] == "FAIL"
    assert probes._component_assessment("ROUT-012", decision(clarification="请说明"))[0] == "PASS"


def test_personal_balance_cannot_be_graded_as_store_operations():
    wrong = RoutedIntent("OPERATIONS", "QUERY_REMAINING_CLASS_HOURS")
    booking = RoutedIntent("BOOKING", "CREATE_BOOKING")
    assert probes._component_assessment("ROUT-016", decision(wrong, booking))[0] == "FAIL"


@pytest.mark.asyncio
async def test_router_exception_is_error_not_pass(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("sensitive text")

    monkeypatch.setattr(probes.RuleIntentRouter, "decide", fail)
    report = await probes.evaluate_intent_observations(load_dataset(DATASET), split="dev")
    assert all(
        r["component_status"] == "ERROR" and r["task_status"] == "UNKNOWN" for r in report["cases"]
    )
    assert "sensitive text" not in str(report)
