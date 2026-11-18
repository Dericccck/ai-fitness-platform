"""In-process *test-double only* step-driver prototype, never a sandbox adapter.

This module has no CLI or production registration. Its injectable lifecycle is
untrusted and may invent observations: results are labeled as test-double
observations and never constitute isolation, review, or release evidence.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

from .contracts import Step, TaskCase
from .report import reduce_trial
from .step_kernel import StepState, UnsupportedSteps, execute_steps, prepare_steps

_T = TypeVar("_T")


class TestOnlyLifecycle(Protocol):
    """Untrusted unit-test double; may not be registered as a sandbox lifecycle."""

    async def start(self, case: TaskCase, trial_id: str) -> None: ...
    async def execute_step(self, step: Step) -> None: ...
    async def observe(self, phase: str) -> dict[str, Any] | None: ...
    async def close(self) -> None: ...


def _row(assertion: Any, status: str, reason: str) -> dict[str, Any]:
    return {
        "id": assertion.id,
        "rule": assertion.rule,
        "phase": assertion.phase,
        "required": assertion.required,
        "risk": assertion.risk,
        "applicable": assertion.applicable,
        "status": status,
        "reason": reason,
    }


async def run_test_trial(case: TaskCase, lifecycle: TestOnlyLifecycle) -> dict[str, Any]:
    """Exercise a case with an untrusted test double; never a real task result.

    Unknown actions and ambiguous assertion phases are rejected before startup.
    Each async operation is bounded; cleanup is attempted in a separate bounded
    finally block. Cancellation is propagated only after the cleanup attempt.
    No raw messages, exceptions, observations or evidence references are returned.
    """
    started = time.monotonic()
    deadline = started + case.timeouts.trial_seconds
    trial_id = uuid4().hex
    result: dict[str, Any] = {
        "trial_id": trial_id,
        "case_id": case.case_id,
        "result_origin": "untrusted_in_process_test_double",
        "task_status": "UNKNOWN",
        "executed": False,
        "status": "INCONCLUSIVE",
        "component_status": "NOT_RUN",
        "reason": None,
        "cleanup_status": "NOT_STARTED",
        "phase_observations": [],
        "assertions": [],
    }
    try:
        requests = prepare_steps(case)
    except UnsupportedSteps:
        result.update(status="SKIPPED", reason="UNSUPPORTED_STEP_OR_AMBIGUOUS_PHASE")
        result["assertions"] = [
            _row(a, "SKIPPED" if a.applicable else "NA", "STEP_NOT_EXECUTED")
            for a in case.expected.assertions
        ]
        return result

    async def bounded(operation: Awaitable[_T]) -> _T:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Close a newly created coroutine rather than leaving it unawaited.
            if hasattr(operation, "close"):
                operation.close()
            raise TimeoutError
        return await asyncio.wait_for(operation, timeout=min(case.timeouts.step_seconds, remaining))

    state = StepState()
    failed: str | None = None
    try:
        await bounded(lifecycle.start(case, trial_id))
        result["executed"] = True
        operations = execute_steps(case, requests, state, evidence_origin="untrusted_test_double")
        request = next(operations, None)
        while request is not None:
            observation = None
            if request.kind == "action":
                await bounded(lifecycle.execute_step(request.step))
            else:
                observation = await bounded(lifecycle.observe(cast(str, request.step.phase)))
            try:
                request = operations.send(observation)
            except StopIteration:
                request = None
    except TimeoutError:
        failed = "ERROR"
        result["reason"] = "TEST_OPERATION_TIMEOUT"
    except Exception:  # noqa: BLE001 - exceptions may contain raw payloads or credentials
        failed = "ERROR"
        result["reason"] = "TEST_OPERATION_ERROR"
    finally:
        try:
            await asyncio.wait_for(lifecycle.close(), timeout=min(case.timeouts.step_seconds, 10))
            result["cleanup_status"] = "COMPLETE"
        except Exception:  # noqa: BLE001 - no raw exception data in result
            failed = "ERROR"
            result["reason"] = "TEST_CLEANUP_ERROR"
            result["cleanup_status"] = "FAILED"
    for assertion in case.expected.assertions:
        if assertion.id not in state.assertions:
            state.assertions[assertion.id] = _row(
                assertion,
                "NA" if not assertion.applicable else "UNKNOWN",
                "NOT_APPLICABLE" if not assertion.applicable else "EVIDENCE_NOT_OBSERVED",
            )
    result["assertions"] = [state.assertions[a.id] for a in case.expected.assertions]
    result["phase_observations"] = state.phase_observations
    result["component_status"] = reduce_trial(
        result["assertions"], executed=result["executed"], failure=failed
    )
    # Test-double results cannot decide real task success or failure.
    result["status"] = "INCONCLUSIVE"
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result
