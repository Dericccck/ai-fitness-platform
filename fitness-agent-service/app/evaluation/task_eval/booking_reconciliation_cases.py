"""Case-bound local probes of real confirmation reconciliation worker behavior.

The business write and operation lookup are simulated in isolated SQLite. The
worker and reconciliation binding checks are real application code, but this
is not proof of downstream event delivery or production database integrity.
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
from app.confirmation.models import ConfirmationRecord
from app.confirmation.reconciliation_worker import ConfirmationReconciliationWorker
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayToolOperation

from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .graders.rules import grade_rule
from .report import reduce_trial

_CASES = {"BOOK-029": "delay_worker", "BOOK-031": "reconciliation_pending"}
_MESSAGE = (
    "请预约 org-1 的 student-1 使用 contract-1，coach-1 的 course-1，"
    "2030-01-01 10:00 至 11:00（东八区）。"
)


def supports(case: Any) -> bool:
    return case.case_id in _CASES and matches_frozen_case(case)


class Repository(local.Repository):
    def __init__(self) -> None:
        self.claimed = False
        self.released = False

    async def claim_reconciliation_batch(self, **_: Any) -> list[ConfirmationRecord]:
        if self.claimed:
            return []
        self.claimed = True
        return [self.current]

    async def finish_reconciliation_attempt(self, *_: Any, **__: Any) -> None:
        self.released = True


class Gateway(local.Gateway):
    def __init__(self, repository: Repository, pending: bool) -> None:
        super().__init__(repository, "response_lost")
        self.pending = pending
        self.queries: list[dict[str, Any]] = []

    async def reconcile_tool_operation(self, **kwargs: Any) -> GatewayToolOperation:
        self.queries.append(kwargs)
        return GatewayToolOperation.model_validate(
            {
                "operationId": kwargs["operation_id"],
                "toolId": kwargs["tool_id"],
                "confirmationId": kwargs["confirmation_id"],
                "organizationId": kwargs["organization_id"],
                "actorId": kwargs["actor_id"],
                "payloadHash": kwargs["payload_hash"],
                "status": "UNKNOWN" if self.pending else "SUCCEEDED",
            }
        )


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("unsupported reconciliation fixture")
    started = time.monotonic()
    fault = _CASES[case.case_id]
    repo = Repository()
    gateway = Gateway(repo, fault == "reconciliation_pending")
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repo),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = Supervisor(
        cast(Any, local.Model()),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    executed = False
    failure = None
    checks: dict[str, bool] = {}
    phase_evidence: dict[str, dict[str, Any]] = {}
    evidence: dict[str, Any] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            response = await asyncio.wait_for(agent.chat(_MESSAGE), case.timeouts.step_seconds)
            executed = True
            cid = response.get("confirmation_id")
            if response.get("status") != "CONFIRMATION_REQUIRED" or not isinstance(cid, str):
                raise ValueError("missing confirmation")
            phase_evidence["before_decision"] = gateway.observe()
            try:
                await asyncio.wait_for(agent.decide(cid, "APPROVE"), case.timeouts.step_seconds)
            except HTTPStatusError as exc:
                if exc.response.status_code != 503:
                    raise
            state_before_worker = await agent.confirmation(cid)
            before_attempts = gateway.attempts
            checks["unknown_before_worker"] = (
                state_before_worker.get("execution_status") == "UNKNOWN"
            )
            checks["response_lost_after_commit"] = gateway.response_lost
            checks["worker_delayed"] = not repo.claimed and not gateway.queries
            worker = ConfirmationReconciliationWorker(
                cast(Any, repo), reconciler=service, batch_size=1
            )
            outcome = await asyncio.wait_for(worker.run_once(), case.steps[4].timeout_seconds)
            final = await agent.confirmation(cid)
            current = repo.current
            matching_query = bool(gateway.queries) and gateway.queries[0] == {
                "operation_id": current.request_id,
                "tool_id": current.tool_id,
                "confirmation_id": current.id,
                "organization_id": current.organization_id,
                "actor_id": current.subject_user_id,
                "payload_hash": current.payload_hash,
            }
            snapshot = gateway.observe()
            checks["worker_queried_bound_operation"] = (
                outcome.scanned == 1 and outcome.query_failures == 0 and matching_query
            )
            checks["single_business_effect"] = (
                snapshot["after"]["booking_count"] == 1
                and snapshot["credit_delta"] == -1
                and gateway.attempts == before_attempts == 1
            )
            checks["worker_lease_released"] = repo.released
            checks["expected_reconciliation_state"] = final.get("execution_status") == (
                "UNKNOWN" if gateway.pending else "SUCCEEDED"
            )
            observed_fault = (
                checks["worker_delayed"] and checks["unknown_before_worker"]
                if fault == "delay_worker"
                else gateway.pending and checks["worker_queried_bound_operation"]
            )
            snapshot["fault_injection"] = {
                "fault": fault,
                "injected": True,
                "observed": observed_fault,
                "observation_complete": True,
            }
            phase_evidence["final"] = snapshot
            evidence = {
                "before_worker_execution_status": state_before_worker.get("execution_status"),
                "final_execution_status": final.get("execution_status"),
                "booking_count": snapshot["after"]["booking_count"],
                "credit_delta": snapshot["credit_delta"],
                "gateway_attempts": gateway.attempts,
                "worker_queries": len(gateway.queries),
                "worker_scanned": outcome.scanned,
                "worker_reconciled": outcome.reconciled,
                "worker_query_failures": outcome.query_failures,
                "checks": checks,
            }
    except Exception:  # noqa: BLE001 - preserve state without exposing internals
        failure = "ERROR"
    finally:
        gateway.db.close()
    assertions = []
    for a in case.expected.assertions:
        phase_snapshot = phase_evidence.get(a.phase)
        if phase_snapshot is None:
            result = {
                "status": "UNKNOWN" if a.applicable else "NA",
                "reason": "EVIDENCE_NOT_OBSERVED",
            }
        else:
            result = grade_rule(a.rule, a.parameters, phase_snapshot, a.applicable)
        assertions.append(
            {
                "id": a.id,
                "rule": a.rule,
                "phase": a.phase,
                "required": a.required,
                "risk": a.risk,
                "applicable": a.applicable,
                **result,
                "evidence_origin": "local_sqlite_and_python_reconciliation_worker",
            }
        )
    assertions.extend(
        {
            "id": "supplemental_" + name,
            "rule": "local_reconciliation_worker",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "PASS" if passed else "FAIL",
            "reason": "LOCAL_PROBE_MATCH" if passed else "LOCAL_PROBE_MISMATCH",
            "evidence_origin": "local_sqlite_and_python_reconciliation_worker",
        }
        for name, passed in checks.items()
    )
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
        "reason": "CASE_STEP_ERROR" if failure else None,
        "assertions": assertions,
        "evidence": evidence,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "result_origin": "local_python_reconciliation_worker_with_business_test_double",
        "limitations": [
            "DRAFT_CASE",
            "SCRIPTED_MODEL",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "SYNTHETIC_MESSAGE_REF",
            "NO_INDEPENDENT_BUSINESS_AUDIT",
            "NO_PROOF_OF_DOWNSTREAM_EVENT_DELIVERY",
            "SEMANTIC_REVIEW_UNAVAILABLE",
        ],
    }
