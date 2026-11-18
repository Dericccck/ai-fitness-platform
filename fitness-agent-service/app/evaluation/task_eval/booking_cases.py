"""Execute three booking fault-case drafts against an isolated protocol harness.

The model and business service are deterministic test doubles: these trials
observe confirmation/fault behaviour only and cannot evaluate model reasoning.
The fixture message fills a missing planned reference; it is not a reviewed case.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPStatusError
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService

from . import (
    booking_boundary_cases,
    booking_java_cases,
    booking_mutation_cases,
    booking_positive_cases,
    booking_reconciliation_cases,
)
from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .graders.rules import grade_rule
from .report import reduce_trial

_FAULTS = {
    "BOOK-026": ("duplicate_decision", "duplicate"),
    "BOOK-027": ("drop_business_response", "response_lost"),
    "BOOK-028": ("business_timeout", "timeout"),
}
_MESSAGE = (
    "请预约 org-1 的 student-1 使用 contract-1，coach-1 的 course-1，"
    "2030-01-01 10:00 至 11:00（东八区）。"
)


def _supports_reject(case: Any) -> bool:
    if case.case_id != "BOOK-018" or case.primary_scenario != "booking":
        return False
    steps = case.steps
    return (
        [s.type for s in steps] == ["chat", "assert", "decision", "await_terminal", "assert"]
        and steps[0].message_ref == "planned-book-018.booking_request"
        and steps[1].phase == "before_decision"
        and steps[2].decision == "REJECT"
        and steps[2].confirmation_ref == "last.confirmation_id"
        and steps[3].target == "confirmation"
        and steps[3].expected_authorization_status == "REJECTED"
        and steps[3].expected_execution_status == "NOT_STARTED"
        and steps[4].phase == "final"
    )


def supports(case: Any) -> bool:
    """Only accept the authored step shape; a matching ID is not sufficient."""
    if not matches_frozen_case(case):
        return False
    if (
        booking_java_cases.supports(case)
        or booking_positive_cases.supports(case)
        or booking_boundary_cases.supports(case)
        or booking_mutation_cases.supports(case)
        or booking_reconciliation_cases.supports(case)
        or _supports_reject(case)
    ):
        return True
    setting = _FAULTS.get(case.case_id)
    if not setting or case.primary_scenario != "booking":
        return False
    fault, _ = setting
    steps = case.steps
    expected_types = ["chat", "assert", "inject_fault", "decision"]
    if fault == "duplicate_decision":
        expected_types.append("decision")
    expected_types.extend(["await_terminal", "assert"])
    return (
        [step.type for step in steps] == expected_types
        and steps[0].message_ref == f"planned-{case.case_id.lower()}.booking_request"
        and steps[1].phase == "before_decision"
        and steps[2].fault == fault
        and steps[-1].phase == "final"
        and all(
            step.decision == "APPROVE" and step.confirmation_ref == "last.confirmation_id"
            for step in steps
            if step.type == "decision"
        )
        and steps[-2].target == "confirmation"
        and not steps[-2].expected_authorization_status
        and not steps[-2].expected_execution_status
    )


def _assertion(assertion: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    result = grade_rule(assertion.rule, assertion.parameters, evidence, assertion.applicable)
    return {
        "id": assertion.id,
        "rule": assertion.rule,
        "phase": assertion.phase,
        "required": assertion.required,
        "risk": assertion.risk,
        "applicable": assertion.applicable,
        "status": result["status"],
        "reason": result["reason"],
        "evidence_origin": "local_sqlite_and_python_api_test_doubles",
    }


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    """Run supported draft steps; a partially graded draft remains inconclusive."""
    if not supports(case):
        raise ValueError("unsupported booking fixture or step sequence")
    if booking_java_cases.supports(case):
        return await booking_java_cases.run(case, index=index, seed=seed)
    if booking_positive_cases.supports(case):
        return await booking_positive_cases.run(case, index=index, seed=seed)
    if booking_boundary_cases.supports(case):
        return await booking_boundary_cases.run(case, index=index, seed=seed)
    if booking_reconciliation_cases.supports(case):
        return await booking_reconciliation_cases.run(case, index=index, seed=seed)
    if booking_mutation_cases.supports(case):
        return await booking_mutation_cases.run(case, index=index, seed=seed)
    if _supports_reject(case):
        start = time.monotonic()
        probe = await local.evaluate_case("reject")
        # The referenced message and actor/action grading parameters are absent
        # from the draft. Report the protocol probe without inventing them.
        assertions = [
            {
                "id": a.id,
                "rule": a.rule,
                "phase": a.phase,
                "required": a.required,
                "risk": a.risk,
                "applicable": a.applicable,
                "status": "UNKNOWN" if a.applicable else "NA",
                "reason": "DRAFT_CONTRACT_OR_SEMANTIC_JUDGMENT_MISSING"
                if a.applicable
                else "NOT_APPLICABLE",
            }
            for a in case.expected.assertions
        ]
        assertions.extend(
            {
                "id": f"supplemental_{name}",
                "rule": "local_rejection_probe",
                "phase": "final",
                "required": True,
                "risk": None,
                "applicable": True,
                "status": "PASS" if passed else "FAIL",
                "reason": "LOCAL_PROBE_MATCH" if passed else "LOCAL_PROBE_MISMATCH",
            }
            for name, passed in probe.get("checks", {}).items()
            if probe["status"] != "ERROR"
        )
        executed = bool(probe.get("checks", {}).get("chat_api_succeeded"))
        failure = "ERROR" if probe["status"] == "ERROR" else None
        return {
            "trial_id": uuid4().hex,
            "case_id": case.case_id,
            "family_id": case.family_id,
            "split": case.split,
            "primary_scenario": case.primary_scenario,
            "delivery_scope": case.delivery_scope,
            "capability_status": case.capability_status,
            "trial_index": index,
            "seed": seed,
            "executed": executed,
            "status": reduce_trial(assertions, executed=executed, failure=failure),
            "reason": probe.get("reason") if failure else None,
            "assertions": assertions,
            "evidence": probe,
            "duration_ms": round((time.monotonic() - start) * 1000, 3),
            "result_origin": "local_scripted_model_and_business_test_double",
            "limitations": [
                "DRAFT_CASE",
                "SCRIPTED_MODEL",
                "BUSINESS_SERVICE_TEST_DOUBLE",
                "SYNTHETIC_MESSAGE_REF",
                "SEMANTIC_REVIEW_UNAVAILABLE",
            ],
        }
    start = time.monotonic()
    fault, scenario = _FAULTS[case.case_id]
    row: dict[str, Any] = {
        "trial_id": uuid4().hex,
        "case_id": case.case_id,
        "family_id": case.family_id,
        "split": case.split,
        "primary_scenario": case.primary_scenario,
        "delivery_scope": case.delivery_scope,
        "capability_status": case.capability_status,
        "trial_index": index,
        "seed": seed,
        "executed": False,
        "status": "SKIPPED",
        "reason": None,
        "assertions": [],
        "duration_ms": None,
        "result_origin": "local_scripted_model_and_business_test_double",
        "limitations": [
            "DRAFT_CASE",
            "SCRIPTED_MODEL",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "SYNTHETIC_MESSAGE_REF",
            "SEMANTIC_REVIEW_UNAVAILABLE",
        ],
    }
    repository = local.Repository()
    gateway = local.Gateway(repository, scenario)
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    supervisor = Supervisor(
        cast(Any, local.Model()),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = supervisor
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    observed: dict[str, dict[str, Any]] = {}
    supplemental: list[dict[str, Any]] = []
    failure = None
    decision_count = 0
    fault_injected = False
    confirmation_id: str | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            for step in case.steps:
                if step.type == "chat":
                    response = await asyncio.wait_for(
                        agent.chat(_MESSAGE), case.timeouts.step_seconds
                    )
                    row["executed"] = True
                    confirmation_id = response.get("confirmation_id")
                    if response.get("status") != "CONFIRMATION_REQUIRED" or not isinstance(
                        confirmation_id, str
                    ):
                        raise ValueError("confirmation missing")
                elif step.type == "inject_fault":
                    fault_injected = True
                elif step.type == "decision":
                    if not confirmation_id or not fault_injected:
                        raise ValueError("invalid execution order")
                    decision_count += 1
                    try:
                        await asyncio.wait_for(
                            agent.decide(confirmation_id, "APPROVE"), case.timeouts.step_seconds
                        )
                    except HTTPStatusError as exc:
                        if (
                            scenario not in {"response_lost", "timeout"}
                            or exc.response.status_code != 503
                        ):
                            raise
                elif step.type == "await_terminal":
                    if not confirmation_id:
                        raise ValueError("missing confirmation")
                    state = await asyncio.wait_for(
                        agent.confirmation(confirmation_id), step.timeout_seconds
                    )
                    if state.get("execution_status") not in {
                        "SUCCEEDED",
                        "FAILED_FINAL",
                        "UNKNOWN",
                        "NOT_STARTED",
                    }:
                        raise ValueError("confirmation not settled")
                elif step.type == "assert":
                    evidence = gateway.observe()
                    if fault == "duplicate_decision":
                        triggered = decision_count == 2
                    elif fault == "drop_business_response":
                        triggered = gateway.response_lost and gateway.attempts > 0
                    else:
                        triggered = (
                            gateway.attempts > 0
                            and repository.current.execution_status == "UNKNOWN"
                        )
                    evidence["fault_injection"] = {
                        "fault": fault,
                        "injected": fault_injected,
                        "observed": triggered,
                        "observation_complete": True,
                    }
                    evidence["evidence_refs"].append("local-python:confirmation-and-fault")
                    for assertion in case.expected.assertions:
                        if assertion.phase == step.phase:
                            observed[assertion.id] = _assertion(assertion, evidence)
        row["evidence"] = {
            "booking_count": gateway.observe()["after"]["booking_count"],
            "credit_delta": gateway.observe()["credit_delta"],
            "gateway_attempts": gateway.attempts,
            "decision_requests": decision_count,
            "authorization_status": repository.current.authorization_status,
            "execution_status": repository.current.execution_status,
        }
        # A safety check supplements the draft's semantic rubric; it never
        # replaces that rubric or upgrades the case to an overall PASS.
        snapshot = gateway.observe()
        count = snapshot["after"]["booking_count"]
        delta = snapshot["credit_delta"]
        safe = count <= 1 and delta in {0, -1} and delta == -count
        if fault == "duplicate_decision":
            safe = safe and count == 1 and delta == -1
        supplemental.append(
            {
                "id": "supplemental_single_effect",
                "rule": "local_single_effect",
                "phase": "final",
                "required": True,
                "risk": "duplicate_write",
                "applicable": True,
                "status": "PASS" if safe else "FAIL",
                "reason": "LOCAL_SQLITE_EFFECT_MATCH" if safe else "LOCAL_SQLITE_EFFECT_MISMATCH",
                "evidence_origin": "local_sqlite_business_test_double",
            }
        )
    except Exception:  # noqa: BLE001 - preserve evidence without exposing arbitrary errors
        failure = "ERROR"
        row["reason"] = "CASE_STEP_ERROR"
    finally:
        gateway.db.close()
    for assertion in case.expected.assertions:
        if assertion.id not in observed:
            observed[assertion.id] = {
                "id": assertion.id,
                "rule": assertion.rule,
                "phase": assertion.phase,
                "required": assertion.required,
                "risk": assertion.risk,
                "applicable": assertion.applicable,
                "status": "UNKNOWN" if assertion.applicable else "NA",
                "reason": "EVIDENCE_NOT_OBSERVED" if assertion.applicable else "NOT_APPLICABLE",
            }
    row["assertions"] = [observed[a.id] for a in case.expected.assertions] + supplemental
    row["status"] = reduce_trial(row["assertions"], executed=row["executed"], failure=failure)
    row["duration_ms"] = round((time.monotonic() - start) * 1000, 3)
    return row
