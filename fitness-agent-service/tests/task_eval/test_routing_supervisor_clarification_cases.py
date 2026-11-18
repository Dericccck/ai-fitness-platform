from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.routing import RouteDecision, RoutedIntent
from app.evaluation.task_eval import routing_supervisor_clarification_cases as probes
from app.evaluation.task_eval.dataset import load_dataset
from app.infrastructure.model_gateway import ModelConfigurationError

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_supervisor_delivers_fallback_without_domain_model_or_tools():
    dataset = load_dataset(DATASET)
    for split, expected_count in (("dev", 6), ("test", 2)):
        report = await probes.evaluate_supervisor_clarification_observations(dataset, split=split)
        assert len(report["cases"]) == expected_count
        for row in report["cases"]:
            assert row["component_status"] == "PASS"
            assert row["task_status"] == "UNKNOWN"
            assert row["observed"]["router_stub_calls"] == 1
            assert row["observed"]["domain_stub_calls"] == 0
            assert row["observed"]["tool_steps"] == 0
            assert row["observed"]["matches_generic_fallback"]


@pytest.mark.asyncio
async def test_case_drift_never_invokes_supervisor(monkeypatch):
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-009")
    changed = case.model_copy(update={"actor": case.actor.model_copy(update={"role": "ADMIN"})})

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")

    monkeypatch.setattr(probes, "_run", forbidden)
    report = await probes.evaluate_supervisor_clarification_observations(
        replace(dataset, cases=[changed]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "CASE_CONTRACT_CHANGED"


@pytest.mark.asyncio
async def test_failure_to_stop_after_unavailable_model_is_detected(monkeypatch):
    async def broken(self, *args, **kwargs):
        try:
            await self.models.chat_json_with_usage([])
        except ModelConfigurationError:
            pass
        return RouteDecision(
            intents=(RoutedIntent("FITNESS_COACHING", "FITNESS_KNOWLEDGE_QA"),),
            source="FALLBACK",
            confidence=0.0,
            reason_code="FAULT",
            classifier_version="test",
        )

    monkeypatch.setattr(probes.HybridIntentRouter, "decide", broken)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-009")
    report = await probes.evaluate_supervisor_clarification_observations(
        replace(dataset, cases=[case]), split="dev"
    )
    row = report["cases"][0]
    assert row["component_status"] == "FAIL"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["domain_stub_calls"] == 1


@pytest.mark.asyncio
async def test_unavailable_model_not_reached_cannot_pass(monkeypatch):
    async def never_calls_model(self, *args, **kwargs):
        return RouteDecision(
            intents=(),
            source="FALLBACK",
            confidence=0.0,
            reason_code="TEST",
            classifier_version="test",
            clarification="请说明",
        )

    monkeypatch.setattr(probes.HybridIntentRouter, "decide", never_calls_model)
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "ROUT-009")
    report = await probes.evaluate_supervisor_clarification_observations(
        replace(dataset, cases=[case]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"
    assert report["cases"][0]["reason"] == "MODEL_UNAVAILABLE_PRECONDITION_NOT_OBSERVED"
