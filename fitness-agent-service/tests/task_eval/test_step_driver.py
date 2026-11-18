import asyncio
import json
from pathlib import Path

import pytest

from app.evaluation.task_eval.contracts import Assertion, Step
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.runner import execute
from app.evaluation.task_eval.test_step_driver import run_test_trial

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def sample_case():
    case = load_dataset(DATASET).cases[0]
    steps = [
        Step(type="chat", message="secret user input"),
        Step(type="assert", phase="before"),
        Step(type="decision", decision="APPROVE", confirmation_ref="last.confirmation_id"),
        Step(type="inject_fault", fault="duplicate_event"),
        Step(type="await_terminal", target="confirmation", timeout_seconds=1),
        Step(type="trigger_event", event_ref="synthetic.event"),
        Step(type="assert", phase="final"),
    ]
    assertions = [
        Assertion(
            id="route",
            rule="route_allowed",
            phase="before",
            required=True,
            parameters={"allowed_routes": ["BOOKING"]},
        ),
        Assertion(
            id="write",
            rule="no_unconfirmed_business_write",
            phase="final",
            required=True,
            risk="unconfirmed_write",
            parameters={},
        ),
    ]
    return case.model_copy(
        update={
            "steps": steps,
            "expected": case.expected.model_copy(update={"assertions": assertions}),
        }
    )


class FakeLifecycle:
    def __init__(self):
        self.events = []
        self.evidence = {
            "before": {"routes": ["BOOKING"], "evidence_refs": ["private-before"]},
            "final": {"commits": [], "audit_complete": True, "evidence_refs": ["private-final"]},
        }

    async def start(self, case, trial_id):
        self.events.append("start")

    async def execute_step(self, step):
        self.events.append(step.type)

    async def observe(self, phase):
        self.events.append("observe:" + phase)
        return self.evidence.get(phase)

    async def close(self):
        self.events.append("close")


@pytest.mark.asyncio
async def test_order_phase_evidence_and_test_only_result():
    adapter = FakeLifecycle()
    report = await run_test_trial(sample_case(), adapter)
    assert adapter.events == [
        "start",
        "chat",
        "observe:before",
        "decision",
        "inject_fault",
        "await_terminal",
        "trigger_event",
        "observe:final",
        "close",
    ]
    assert report["status"] == "INCONCLUSIVE" and report["task_status"] == "UNKNOWN"
    assert report["component_status"] == "PASS" and report["cleanup_status"] == "COMPLETE"
    assert [row["status"] for row in report["assertions"]] == ["PASS", "PASS"]
    assert [row["phase"] for row in report["phase_observations"]] == ["before", "final"]
    assert (
        report["phase_observations"][0]["evidence_ref_sha256"]
        != report["phase_observations"][1]["evidence_ref_sha256"]
    )
    assert "private" not in json.dumps(report) and "secret user input" not in json.dumps(report)


@pytest.mark.asyncio
async def test_missing_evidence_is_unknown_and_not_pass():
    adapter = FakeLifecycle()
    adapter.evidence["final"] = {"commits": [], "audit_complete": True}
    report = await run_test_trial(sample_case(), adapter)
    assert [row["status"] for row in report["assertions"]] == ["PASS", "UNKNOWN"]
    assert report["component_status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_failure_survives_later_exception_and_is_redacted():
    adapter = FakeLifecycle()
    adapter.evidence["before"]["routes"] = ["OTHER"]

    async def fail_step(step):
        if step.type == "decision":
            raise RuntimeError("secret payload")

    adapter.execute_step = fail_step
    report = await run_test_trial(sample_case(), adapter)
    assert report["component_status"] == "FAIL"
    assert [row["status"] for row in report["assertions"]] == ["FAIL", "UNKNOWN"]
    assert report["cleanup_status"] == "COMPLETE" and "secret" not in json.dumps(report)
    assert report["status"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_start_error_and_cleanup_error_are_independently_reported():
    adapter = FakeLifecycle()

    async def fail_start(case, trial_id):
        raise RuntimeError("private")

    async def fail_close():
        adapter.events.append("close")
        raise RuntimeError("private")

    adapter.start, adapter.close = fail_start, fail_close
    report = await run_test_trial(sample_case(), adapter)
    assert not report["executed"] and report["cleanup_status"] == "FAILED"
    assert adapter.events == ["close"] and report["component_status"] == "ERROR"
    assert "private" not in json.dumps(report)


@pytest.mark.asyncio
async def test_unsupported_or_ambiguous_steps_never_start_adapter():
    adapter = FakeLifecycle()
    case = sample_case()
    unknown = case.steps[0].model_copy(update={"type": "unsupported"})
    report = await run_test_trial(case.model_copy(update={"steps": [unknown]}), adapter)
    assert report["status"] == "SKIPPED" and not adapter.events
    duplicate = case.model_copy(
        update={"steps": [*case.steps, Step(type="assert", phase="before")]}
    )
    report = await run_test_trial(duplicate, adapter)
    assert report["status"] == "SKIPPED" and not adapter.events


@pytest.mark.asyncio
async def test_cancellation_propagates_after_cleanup():
    adapter = FakeLifecycle()
    waiting = asyncio.Event()

    async def block_step(step):
        waiting.set()
        await asyncio.Event().wait()

    adapter.execute_step = block_step
    task = asyncio.create_task(run_test_trial(sample_case(), adapter))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.events[-1] == "close"


@pytest.mark.asyncio
async def test_timeout_attempts_cleanup():
    adapter = FakeLifecycle()
    case = sample_case()
    case = case.model_copy(
        update={"timeouts": case.timeouts.model_copy(update={"step_seconds": 0.01})}
    )

    async def block_step(step):
        await asyncio.sleep(10)

    adapter.execute_step = block_step
    report = await run_test_trial(case, adapter)
    assert report["reason"] == "TEST_OPERATION_TIMEOUT"
    assert report["cleanup_status"] == "COMPLETE" and adapter.events[-1] == "close"


def test_test_lifecycle_cannot_be_passed_to_production_runner():
    with pytest.raises(ValueError):
        execute(
            load_dataset(DATASET), split="dev", mode="integration_sandbox", adapter=FakeLifecycle()
        )


@pytest.mark.asyncio
async def test_shared_kernel_matches_synchronous_runner_phases_and_scores():
    from app.evaluation.task_eval.adapters.synthetic import SyntheticAdapter
    from app.evaluation.task_eval.contracts import Review
    from app.evaluation.task_eval.runner import run_trial

    case = sample_case().model_copy(
        update={
            "capability_status": "supported",
            "review": Review(
                status="reviewed",
                annotator_ref="test-only",
                reviewer_ref="test-only",
                reviewed_at="test-only",
                decision_ref="test-only",
            ),
        }
    )
    fake = FakeLifecycle()
    async_result = await run_test_trial(case, fake)
    sync_result = run_trial(case, 0, 0, "contract", SyntheticAdapter({case.case_id: fake.evidence}))
    assert sync_result["status"] == "PASS"
    assert async_result["status"] == "INCONCLUSIVE"
    assert async_result["phase_observations"] == sync_result["phase_observations"]
    assert [a["status"] for a in async_result["assertions"]] == [
        a["status"] for a in sync_result["assertions"]
    ]
    assert [a["evidence_ref_sha256"] for a in async_result["assertions"]] == [
        a["evidence_ref_sha256"] for a in sync_result["assertions"]
    ]
    assert all(a["evidence_origin"] == "synthetic" for a in sync_result["assertions"])


def test_runner_rejects_ambiguous_phases_before_start():
    from app.evaluation.task_eval.adapters.synthetic import SyntheticAdapter
    from app.evaluation.task_eval.contracts import Review
    from app.evaluation.task_eval.runner import run_trial

    case = sample_case()
    case = case.model_copy(
        update={
            "steps": [*case.steps, Step(type="assert", phase="before")],
            "capability_status": "supported",
            "review": Review(
                status="reviewed",
                annotator_ref="test-only",
                reviewer_ref="test-only",
                reviewed_at="test-only",
                decision_ref="test-only",
            ),
        }
    )
    adapter = SyntheticAdapter({case.case_id: {}})
    result = run_trial(case, 0, 0, "contract", adapter)
    assert result["status"] == "SKIPPED"
    assert result["reason"] == "UNSUPPORTED_STEP_OR_AMBIGUOUS_PHASE"
    assert result["executed"] is False
