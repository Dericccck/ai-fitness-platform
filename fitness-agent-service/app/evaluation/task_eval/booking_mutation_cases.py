"""Bounded reschedule/cancel protocol probes using synthetic, isolated SQLite state.

The scripted model supplies missing appointment details: these probes assess the
confirmation protocol, not interpretation of the incomplete draft messages.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPStatusError
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.routing import RouteDecision, RoutedIntent
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayConflictError, GatewayRequestContext
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .graders.rules import grade_rule
from .report import reduce_trial

_OLD = "2030-01-01T02:00:00Z"
_NEW = "2030-01-02T02:00:00Z"
_CASES = {
    "BOOK-032": ("把预约改到指定时间", "reschedule", "APPROVE"),
    "BOOK-033": ("把预约改到满员时段", "conflict", "APPROVE"),
    "BOOK-034": ("取消我指定的预约", "cancel", "APPROVE"),
    "BOOK-035": ("发起取消后拒绝确认", "cancel", "REJECT"),
}


def supports(case: Any) -> bool:
    spec = _CASES.get(case.case_id)
    if (
        not spec
        or not matches_frozen_case(case)
        or case.primary_scenario != "booking"
        or case.subcategory != "reschedule_or_cancel"
    ):
        return False
    steps = case.steps
    return (
        [s.type for s in steps] == ["chat", "assert", "decision", "await_terminal", "assert"]
        and steps[0].message == spec[0]
        and steps[1].phase == "before_decision"
        and steps[2].decision == spec[2]
        and steps[2].confirmation_ref == "last.confirmation_id"
        and steps[3].target == "confirmation"
        and not steps[3].expected_authorization_status
        and not steps[3].expected_execution_status
        and steps[4].phase == "final"
    )


class Gateway(local.Gateway):
    def __init__(self, repo: local.Repository, scenario: str) -> None:
        super().__init__(repo, scenario)
        self.db.execute("CREATE TABLE appointment (id TEXT, start_time TEXT, status TEXT)")
        self.db.execute("INSERT INTO appointment VALUES ('appointment-1', ?, 'ACTIVE')", (_OLD,))
        self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        row = self.db.execute("SELECT id, start_time, status FROM appointment").fetchone()
        return {
            "id": row[0],
            "start_time": row[1],
            "status": row[2],
            "credits": self.db.execute("SELECT remaining FROM credits").fetchone()[0],
        }

    def observe(self) -> dict[str, Any]:
        evidence = super().observe()
        current = self.snapshot()
        evidence["before"] = {"booking_count": 1, "credit_count": 10}
        evidence["after"] = {
            "booking_count": int(current["status"] == "ACTIVE"),
            "credit_count": current["credits"],
        }
        evidence["credit_delta"] = current["credits"] - 10
        evidence["evidence_refs"].append("local-sqlite:appointment")
        return evidence

    def _authorize(self, context: GatewayRequestContext, payload: dict[str, Any]) -> None:
        if (
            context.confirmation_token != "local-test-token"
            or self.repository.current.authorization_status != "APPROVED"
            or self.repository.current.execution_status != "RUNNING"
        ):
            raise RuntimeError("write attempted without authorization")
        if (
            payload.get("appointmentId") != "appointment-1"
            or payload.get("organizationId") != "org-1"
            or payload.get("expectedStartTime") != _OLD
        ):
            raise GatewayConflictError("fixture mismatch", status_code=409)

    async def reschedule_booking(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.attempts += 1
        self._authorize(context, payload)
        if self.scenario == "conflict":
            raise GatewayConflictError("target full", status_code=409)
        with self.db:
            self.db.execute(
                "UPDATE appointment SET start_time = ? WHERE id = 'appointment-1'",
                (payload["startTime"],),
            )
            self.db.execute("INSERT INTO audit VALUES (1)")
        return {"id": "appointment-1"}

    async def cancel_booking(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.attempts += 1
        self._authorize(context, payload)
        with self.db:
            self.db.execute(
                "UPDATE appointment SET status = 'CANCELLED' WHERE id = 'appointment-1'"
            )
            self.db.execute("UPDATE credits SET remaining = remaining + 1")
            self.db.execute("INSERT INTO audit VALUES (1)")
        return {"id": "appointment-1"}


class Model:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls = 0

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]], **kwargs: Any
    ) -> ModelTurn:
        self.calls += 1
        if self.calls != 1:
            return ModelTurn(content="请求已处理。", tool_calls=())
        reschedule = self.scenario in {"reschedule", "conflict"}
        payload = {
            "organization_id": "org-1",
            "appointment_id": "appointment-1",
            "expected_start_time": _OLD,
        }
        if reschedule:
            payload.update(coach_id="coach-1", start_time=_NEW, end_time="2030-01-02T03:00:00Z")
        return ModelTurn(
            content="",
            tool_calls=(
                ModelToolCall(
                    call_id="mutation",
                    name=(
                        "fitness_booking_reschedule_v1"
                        if reschedule
                        else "fitness_booking_cancel_v1"
                    ),
                    arguments=payload,
                ),
            ),
        )


class Router:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    async def decide(self, *_: Any, **__: Any) -> RouteDecision:
        intent = (
            "RESCHEDULE_BOOKING"
            if self.scenario in {"reschedule", "conflict"}
            else "CANCEL_BOOKING"
        )
        return RouteDecision(
            intents=(RoutedIntent("BOOKING", intent),),
            source="LLM",
            confidence=1.0,
            reason_code="LOCAL_FIXTURE",
            classifier_version="local-fixture",
        )


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("unsupported booking mutation fixture")
    started = time.monotonic()
    _, scenario, decision = _CASES[case.case_id]
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
        "result_origin": "local_scripted_model_and_business_test_double",
        "limitations": [
            "DRAFT_CASE",
            "SCRIPTED_MODEL",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "SYNTHETIC_APPOINTMENT_PARAMETERS",
            "SEMANTIC_REVIEW_UNAVAILABLE",
        ],
    }
    repo = local.Repository()
    gateway = Gateway(repo, scenario)
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
        cast(Any, Model(scenario)),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, Router(scenario)),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    observed: dict[str, dict[str, Any]] = {}
    supplemental: list[dict[str, Any]] = []
    failure = None
    before = gateway.snapshot()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            response = await asyncio.wait_for(
                agent.chat(case.steps[0].message), case.timeouts.step_seconds
            )
            row["executed"] = True
            cid = response.get("confirmation_id")
            if response.get("status") != "CONFIRMATION_REQUIRED" or not isinstance(cid, str):
                raise ValueError("confirmation missing")
            pending_snapshot = gateway.snapshot()
            evidence = gateway.observe()
            for assertion in case.expected.assertions:
                if assertion.phase == "before_decision":
                    result = grade_rule(
                        assertion.rule, assertion.parameters, evidence, assertion.applicable
                    )
                    observed[assertion.id] = {
                        "id": assertion.id,
                        "rule": assertion.rule,
                        "phase": assertion.phase,
                        "required": assertion.required,
                        "risk": assertion.risk,
                        "applicable": assertion.applicable,
                        **result,
                        "evidence_origin": "local_sqlite_business_test_double",
                    }
            try:
                await asyncio.wait_for(
                    agent.decide(cid, cast(Literal["APPROVE", "REJECT"], decision)),
                    case.timeouts.step_seconds,
                )
            except HTTPStatusError as exc:
                if scenario != "conflict" or exc.response.status_code != 503:
                    raise
            state = await asyncio.wait_for(agent.confirmation(cid), case.steps[3].timeout_seconds)
            after = gateway.snapshot()
            evidence = gateway.observe()
            for assertion in case.expected.assertions:
                if assertion.phase == "final":
                    result = grade_rule(
                        assertion.rule, assertion.parameters, evidence, assertion.applicable
                    )
                    observed[assertion.id] = {
                        "id": assertion.id,
                        "rule": assertion.rule,
                        "phase": assertion.phase,
                        "required": assertion.required,
                        "risk": assertion.risk,
                        "applicable": assertion.applicable,
                        **result,
                        "evidence_origin": "local_sqlite_business_test_double",
                    }
            expected_after = dict(before)
            if scenario == "reschedule":
                expected_after["start_time"] = _NEW
            elif scenario == "cancel" and decision == "APPROVE":
                expected_after.update(status="CANCELLED", credits=before["credits"] + 1)
            expected_auth = "REJECTED" if decision == "REJECT" else "APPROVED"
            expected_execution = (
                "NOT_STARTED"
                if decision == "REJECT"
                else "FAILED_FINAL"
                if scenario == "conflict"
                else "SUCCEEDED"
            )
            checks = {
                "unchanged_before_decision": pending_snapshot == before,
                "expected_final_state": after == expected_after,
                "expected_confirmation_status": state.get("authorization_status") == expected_auth
                and state.get("execution_status") == expected_execution,
                "expected_attempt_count": gateway.attempts == (0 if decision == "REJECT" else 1),
            }
            row["evidence"] = {
                "before": before,
                "before_decision": pending_snapshot,
                "after": after,
                "attempts": gateway.attempts,
                "authorization_status": state.get("authorization_status"),
                "execution_status": state.get("execution_status"),
            }
            for name, passed in checks.items():
                supplemental.append(
                    {
                        "id": "supplemental_" + name,
                        "rule": "local_mutation_state",
                        "phase": "final",
                        "required": True,
                        "risk": "unconfirmed_write"
                        if name == "unchanged_before_decision"
                        else None,
                        "applicable": True,
                        "status": "PASS" if passed else "FAIL",
                        "reason": "LOCAL_STATE_MATCH" if passed else "LOCAL_STATE_MISMATCH",
                        "evidence_origin": "local_sqlite_business_test_double",
                    }
                )
    except Exception:  # noqa: BLE001 - preserve outcome without leaking arbitrary errors
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
    row["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return row
