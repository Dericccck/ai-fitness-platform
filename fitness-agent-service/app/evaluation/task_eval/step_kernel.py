"""Shared, synchronous orchestration for the offline runner and test driver.

This pure generator never performs I/O or proves an adapter is trusted. Each host
must enforce its own adapter restrictions, timeout, cancellation and cleanup.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from .contracts import Step, TaskCase
from .graders.rules import grade_rule

_ACTIONS = frozenset({"chat", "decision", "inject_fault", "await_terminal", "trigger_event"})


class UnsupportedSteps(ValueError):
    """An action or observation sequence cannot be represented unambiguously."""


@dataclass(frozen=True)
class StepRequest:
    index: int
    kind: str
    step: Step


@dataclass
class StepState:
    assertions: dict[str, dict[str, Any]] = field(default_factory=dict)
    phase_observations: list[dict[str, Any]] = field(default_factory=list)


def prepare_steps(case: TaskCase) -> tuple[StepRequest, ...]:
    """Validate the complete plan before the caller starts a lifecycle."""
    phases: set[str] = set()
    requests: list[StepRequest] = []
    for index, step in enumerate(case.steps):
        if not isinstance(step.type, str):
            raise UnsupportedSteps("unsupported step")
        if step.type in _ACTIONS:
            requests.append(StepRequest(index, "action", step))
        elif step.type == "assert":
            if not isinstance(step.phase, str) or not step.phase or step.phase in phases:
                raise UnsupportedSteps("invalid or repeated phase")
            phases.add(step.phase)
            requests.append(StepRequest(index, "observe", step))
        else:
            raise UnsupportedSteps("unsupported step")
    if any(assertion.phase not in phases for assertion in case.expected.assertions):
        raise UnsupportedSteps("assertion phase is not observed")
    return tuple(requests)


def _assertion_row(assertion: Any, status: str, reason: str) -> dict[str, Any]:
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


def execute_steps(
    case: TaskCase,
    requests: tuple[StepRequest, ...],
    state: StepState,
    *,
    evidence_origin: str,
    grader_version: str | None = None,
) -> Generator[StepRequest, dict[str, Any] | None, None]:
    """Yield ordered operations; the host sends observations back at each phase."""
    for request in requests:
        observation = yield request
        if request.kind != "observe":
            continue
        phase = request.step.phase
        refs = observation.get("evidence_refs") if isinstance(observation, dict) else None
        hashes = (
            [hashlib.sha256(ref.encode()).hexdigest() for ref in refs]
            if isinstance(refs, list) and refs and all(isinstance(ref, str) and ref for ref in refs)
            else []
        )
        state.phase_observations.append(
            {"step_index": request.index, "phase": phase, "evidence_ref_sha256": hashes}
        )
        for assertion in case.expected.assertions:
            if assertion.phase != phase:
                continue
            grade = grade_rule(
                assertion.rule, assertion.parameters, observation, assertion.applicable
            )
            status = grade.get("status")
            unanswerable_na = (
                assertion.rule == "required_evidence_recalled"
                and assertion.parameters.get("answerable") is False
                and grade.get("reason") == "UNANSWERABLE_RECALL_NA"
            )
            if (
                not isinstance(status, str)
                or status not in {"PASS", "FAIL", "UNKNOWN", "NA"}
                or (assertion.applicable and status == "NA" and not unanswerable_na)
            ):
                status = "UNKNOWN"
            item = _assertion_row(assertion, status, grade.get("reason", "INVALID_GRADER_RESULT"))
            if grader_version is not None:
                item["grader_version"] = grader_version
            if hashes:
                item.update(evidence_ref_sha256=hashes, evidence_origin=evidence_origin)
            metrics = grade.get("metrics")
            if isinstance(metrics, dict):
                item["metrics"] = {
                    key: value
                    for key, value in metrics.items()
                    if isinstance(key, str)
                    and (value is None or (type(value) in (int, float) and math.isfinite(value)))
                }
            state.assertions[assertion.id] = item
