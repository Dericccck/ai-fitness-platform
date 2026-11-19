"""Isolated booking protocol evaluation using the real Python Supervisor.

The deterministic model, in-memory confirmation repository and SQLite business
service are test doubles. No network, production data, or real model is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPError, HTTPStatusError
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.routing import RouteDecision, RoutedIntent
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.models import ConfirmationAction, ConfirmationRecord, ConfirmationStateError
from app.confirmation.repository import ConfirmationNotFound
from app.confirmation.service import ConfirmationService
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import (
    GatewayConflictError,
    GatewayExecutionUnknownError,
    GatewayRequestContext,
)
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from .agent_http import AgentHTTP
from .graders.rules import grade_rule

PAYLOAD = {
    "organization_id": "org-1",
    "student_id": "student-1",
    "contract_id": "contract-1",
    "coach_id": "coach-1",
    "course_id": "course-1",
    "start_time": "2030-01-01T10:00:00+08:00",
    "end_time": "2030-01-01T11:00:00+08:00",
}
IDENTITY = AgentIdentity(
    subject="student-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"STUDENT"}),
    issued_at=1,
    expires_at=2,
)
OTHER = AgentIdentity(
    subject="other",
    organization_ids=frozenset({"org-2"}),
    roles=frozenset({"STUDENT"}),
    issued_at=1,
    expires_at=2,
)


class Repository:
    """Minimal isolated repository substitute; never represents PostgreSQL guarantees."""

    current: ConfirmationRecord

    async def create(
        self, confirmation_id: str, action: ConfirmationAction, expires_at: datetime
    ) -> ConfirmationRecord:
        values = vars(action).copy()
        values.pop("trace_id", None)
        self.current = ConfirmationRecord(
            id=confirmation_id,
            protocol_version=1,
            authorization_status="PENDING",
            execution_status="NOT_STARTED",
            version=0,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            **values,
        )
        return self.current

    async def get_for_subject(
        self, cid: str, subject: str, orgs: tuple[str, ...]
    ) -> ConfirmationRecord:
        record = self.current
        if (
            cid != record.id
            or subject != record.subject_user_id
            or record.organization_id not in orgs
        ):
            raise ConfirmationNotFound("not found")
        return record

    async def decide(
        self,
        cid: str,
        subject: str,
        orgs: tuple[str, ...],
        decision: str,
        decision_id: str,
        now: datetime,
        trace: str | None,
        actor: str,
        roles: tuple[str, ...],
    ) -> ConfirmationRecord:
        record = await self.get_for_subject(cid, subject, orgs)
        if record.decision_request_id == decision_id:
            if record.authorization_status != ("APPROVED" if decision == "APPROVE" else "REJECTED"):
                raise ConfirmationStateError("decision conflict")
            return record
        self.current = (
            record.approve(now, decision_id)
            if decision == "APPROVE"
            else record.reject(now, decision_id)
        )
        return self.current

    async def issue_credential_jti(
        self, cid: str, jti: str, now: datetime, trace: str | None
    ) -> ConfirmationRecord:
        self.current = self.current.issue_credential(jti, now)
        return self.current

    async def claim_execution(
        self, cid: str, now: datetime, trace: str | None
    ) -> ConfirmationRecord:
        self.current = self.current.claim_execution(now)
        return self.current

    async def finish_execution(
        self,
        cid: str,
        success: bool,
        now: datetime,
        trace: str | None,
        *,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> ConfirmationRecord:
        self.current = (
            self.current.finish_success(now)
            if success
            else self.current.finish_failure(now, error_code or "UNKNOWN_ERROR", retryable)
        )
        return self.current

    async def mark_unknown(self, cid: str, now: datetime, trace: str | None) -> ConfirmationRecord:
        self.current = self.current.mark_unknown(now)
        return self.current


class TokenIssuer:
    def issue(self, record: ConfirmationRecord, **kwargs: Any) -> str:
        return "local-test-token"


class Gateway:
    def __init__(self, repository: Repository, scenario: str) -> None:
        self.repository = repository
        self.scenario = scenario
        self.attempts = 0
        self.response_lost = False
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE credits (remaining INTEGER NOT NULL); INSERT INTO credits VALUES (10); CREATE TABLE bookings (payload TEXT NOT NULL); CREATE TABLE audit (authorized_before_commit INTEGER NOT NULL);"
        )

    async def create_booking(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.attempts += 1
        if self.scenario == "conflict":
            raise GatewayConflictError("fixture conflict", status_code=409)
        if self.scenario == "timeout":
            raise GatewayExecutionUnknownError("fixture transport timeout")
        authorized = (
            self.repository.current.authorization_status == "APPROVED"
            and self.repository.current.execution_status == "RUNNING"
        )
        if context.confirmation_token != "local-test-token" or not authorized:
            raise RuntimeError("write attempted without authorization")
        with self.db:
            self.db.execute(
                "INSERT INTO bookings VALUES (?)", (json.dumps(payload, sort_keys=True),)
            )
            self.db.execute("UPDATE credits SET remaining = remaining - 1")
            self.db.execute("INSERT INTO audit VALUES (?)", (int(authorized),))
        if self.scenario == "response_lost":
            self.response_lost = True
            raise GatewayExecutionUnknownError("fixture response lost after commit")
        return {"id": "booking-1"}

    def observe(self) -> dict[str, Any]:
        bookings = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM bookings")]
        commits = [
            {"authorized_before_commit": bool(row[0])}
            for row in self.db.execute("SELECT authorized_before_commit FROM audit")
        ]
        return {
            "evidence_refs": [
                "local-sqlite:bookings",
                "local-sqlite:credits",
                "local-sqlite:audit",
            ],
            "after": {
                "booking_count": len(bookings),
                "credit_count": self.db.execute("SELECT remaining FROM credits").fetchone()[0],
            },
            "before": {"booking_count": 0, "credit_count": 10},
            "created_bookings": bookings,
            "credit_delta": self.db.execute("SELECT remaining FROM credits").fetchone()[0] - 10,
            "snapshot_complete": True,
            "audit_complete": True,
            "commits": commits,
        }


class Model:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.payload = dict(PAYLOAD if payload is None else payload)

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]], **kwargs: Any
    ) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="booking", name="fitness_booking_create_v1", arguments=self.payload
                    ),
                ),
            )
        return ModelTurn(content="已处理预约请求。", tool_calls=())


class Router:
    async def decide(self, *_: Any, **__: Any) -> RouteDecision:
        return RouteDecision(
            intents=(RoutedIntent("BOOKING", "CREATE_BOOKING"),),
            source="LLM",
            confidence=1.0,
            reason_code="LOCAL_FIXTURE",
            classifier_version="local-fixture",
        )


class Verifier:
    def verify(self, token: str) -> AgentIdentity:
        if token == "signed-context":
            return IDENTITY
        if token == "other-context":
            return OTHER
        raise ValueError("invalid context")


async def evaluate_case(
    scenario: str, *, message: str = "请帮我预约课程", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    if scenario not in {
        "approve",
        "reject",
        "unauthorized",
        "duplicate",
        "conflict",
        "timeout",
        "response_lost",
    }:
        raise ValueError("unknown scenario")
    repository = Repository()
    gateway = Gateway(repository, scenario)
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, TokenIssuer()),
    )
    supervisor = Supervisor(
        cast(Any, Model(payload)),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, Router()),
    )
    app = FastAPI()
    app.state.context_verifier = Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = supervisor
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    checks: dict[str, bool] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            try:
                response = await agent.chat(message)
            except (HTTPError, TypeError, ValueError):
                return {
                    "case": scenario,
                    "origin": "real_python_supervisor_with_local_test_doubles",
                    "review_status": "draft",
                    "status": "ERROR",
                    "reason": "CHAT_API_ERROR_OR_INVALID_RESPONSE",
                    "checks": {"chat_api_succeeded": False},
                }
            checks["chat_api_succeeded"] = True
            checks["conversation_matches"] = (
                response.get("conversation_id") == agent.conversation_id
            )
            cid = response.get("confirmation_id")
            checks["confirmation_required"] = (
                response.get("status") == "CONFIRMATION_REQUIRED"
                and isinstance(cid, str)
                and bool(cid)
            )
            if (
                not checks["confirmation_required"]
                or not checks["conversation_matches"]
                or not isinstance(cid, str)
            ):
                return {
                    "case": scenario,
                    "origin": "real_python_supervisor_with_local_test_doubles",
                    "review_status": "draft",
                    "status": "FAIL",
                    "checks": checks,
                }
            before = gateway.observe()
            checks["no_write_before_decision"] = (
                grade_rule("booking_and_credit_delta_zero", {}, before)["status"] == "PASS"
            )
            checks["complete_audit_before_decision"] = (
                grade_rule("no_unconfirmed_business_write", {}, before)["status"] == "PASS"
                and before["commits"] == []
            )
            if scenario == "unauthorized":
                result = await client.post(
                    f"/api/v1/agent/confirmations/{cid}/decisions",
                    headers={"X-Agent-Context": "other-context"},
                    json={"decision": "APPROVE", "decision_request_id": "other"},
                )
                checks["unauthorized_denied"] = result.status_code == 404
            else:
                if scenario in {"conflict", "timeout", "response_lost"}:
                    checks["request_error_expected"] = False
                try:
                    result_body = await agent.decide(
                        cid, "REJECT" if scenario == "reject" else "APPROVE"
                    )
                    checks["authorization"] = result_body["authorization_status"] == (
                        "REJECTED" if scenario == "reject" else "APPROVED"
                    )
                    if scenario in {"approve", "duplicate"}:
                        again = await agent.decide(cid, "APPROVE")
                        checks["repeat_same_result"] = (
                            again["execution_status"] == result_body["execution_status"]
                        )
                except HTTPStatusError as exc:
                    # A 503 means the API did not claim business success; verify state below.
                    checks["request_error_expected"] = (
                        scenario in {"conflict", "timeout", "response_lost"}
                        and exc.response.status_code == 503
                    )
        evidence = gateway.observe()
        evidence["idempotency_checked"] = scenario in {"approve", "duplicate"}
        checks["no_unconfirmed_write"] = (
            grade_rule("no_unconfirmed_business_write", {}, evidence)["status"] == "PASS"
        )
        if scenario in {"approve", "duplicate"}:
            checks["one_booking_one_credit"] = (
                grade_rule(
                    "booking_created_once",
                    {
                        "booking": {
                            "organizationId": "org-1",
                            "studentId": "student-1",
                            "contractId": "contract-1",
                            "coachId": "coach-1",
                            "courseId": "course-1",
                            "startTime": "2030-01-01T02:00:00Z",
                            "endTime": "2030-01-01T03:00:00Z",
                        },
                        "credit_delta": -1,
                    },
                    evidence,
                )["status"]
                == "PASS"
            )
            checks["execution_succeeded"] = repository.current.execution_status == "SUCCEEDED"
        elif scenario == "response_lost":
            checks["fault_triggered_after_commit"] = gateway.response_lost
            checks["single_attempt"] = gateway.attempts == 1
            checks["single_committed_booking_and_credit"] = (
                len(evidence["created_bookings"]) == 1
                and evidence["credit_delta"] == -1
                and len(evidence["commits"]) == 1
            )
            checks["execution_unknown"] = repository.current.execution_status == "UNKNOWN"
        else:
            checks["no_business_change"] = (
                grade_rule("booking_and_credit_delta_zero", {}, evidence)["status"] == "PASS"
            )
            if scenario == "reject":
                checks["rejected"] = repository.current.authorization_status == "REJECTED"
            if scenario == "conflict":
                checks["conflict_not_success"] = (
                    repository.current.execution_status == "FAILED_FINAL"
                )
            if scenario == "timeout":
                checks["timeout_unknown"] = repository.current.execution_status == "UNKNOWN"
        return {
            "case": scenario,
            "origin": "real_python_supervisor_with_local_test_doubles",
            "review_status": "draft",
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "business": {
                "bookings": len(evidence["created_bookings"]),
                "credit_delta": evidence["credit_delta"],
                "audited_commits": len(evidence["commits"]),
                "gateway_attempts": gateway.attempts,
                "response_lost_after_commit": gateway.response_lost,
            },
            "authorization_status": repository.current.authorization_status,
            "execution_status": repository.current.execution_status,
        }
    finally:
        gateway.db.close()


async def evaluate_suite() -> dict[str, Any]:
    cases = []
    for name in (
        "approve",
        "reject",
        "unauthorized",
        "duplicate",
        "conflict",
        "timeout",
        "response_lost",
    ):
        try:
            cases.append(await asyncio.wait_for(evaluate_case(name), timeout=30))
        except Exception:  # noqa: BLE001 - retain other cases without exposing fixture data
            cases.append({"case": name, "status": "ERROR", "reason": "LOCAL_CASE_ERROR"})
    statuses = [case["status"] for case in cases]
    return {
        "suite": "local-booking-protocol-v1",
        "origin": "real_python_supervisor_with_local_test_doubles",
        "status": "FAIL" if "FAIL" in statuses else "INCOMPLETE" if "ERROR" in statuses else "PASS",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=(
            "approve",
            "reject",
            "unauthorized",
            "duplicate",
            "conflict",
            "timeout",
            "response_lost",
        ),
    )
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        result = asyncio.run(evaluate_case(args.case) if args.case else evaluate_suite())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "FAIL": 2, "INCOMPLETE": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
