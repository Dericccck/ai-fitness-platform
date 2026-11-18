"""Distinct case-bound booking components, using only synthetic inputs.

These tests do not assess the case messages or establish contract eligibility,
real-model quality, or Java gateway results. No live model is constructed.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_CASES = {
    "BOOK-001",
    "BOOK-002",
    "BOOK-003",
    "BOOK-004",
    "BOOK-005",
    "BOOK-006",
    "BOOK-007",
    "BOOK-010",
}


class _CoachTimeModel(local.Model):
    async def chat_with_tools(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]], **kwargs: Any
    ) -> ModelTurn:
        self.calls += 1
        if self.calls > 1:
            return ModelTurn(content="请求已处理。", tool_calls=())
        payload = {
            **local.PAYLOAD,
            "coach_id": "coach-2",
            "start_time": "2030-01-02T09:30:00+08:00",
            "end_time": "2030-01-02T10:30:00+08:00",
        }
        return ModelTurn(
            content="",
            tool_calls=(
                ModelToolCall(
                    call_id="coach-time", name="fitness_booking_create_v1", arguments=payload
                ),
            ),
        )


async def _coach_time_probe(message: str) -> dict[str, Any]:
    """Exercise confirmation with fixed inputs; the scripted model ignores message semantics."""
    repo = local.Repository()
    gateway = local.Gateway(repo, "approve")
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
        cast(Any, _CoachTimeModel()),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    checks: dict[str, bool] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            response = await agent.chat(message)
            checks["chat_api_succeeded"] = True
            cid = response.get("confirmation_id")
            checks["confirmation_required"] = response.get(
                "status"
            ) == "CONFIRMATION_REQUIRED" and isinstance(cid, str)
            if not isinstance(cid, str) or not checks["confirmation_required"]:
                return {"status": "FAIL", "checks": checks}
            before = gateway.observe()
            checks["no_write_before_decision"] = (
                before["after"]["booking_count"] == 0 and before["credit_delta"] == 0
            )
            decision = await agent.decide(cid, "APPROVE")
            after = gateway.observe()
            rows = after["created_bookings"]
            checks["one_exact_coach_and_time"] = (
                len(rows) == 1
                and rows[0].get("coachId") == "coach-2"
                and rows[0].get("startTime") == "2030-01-02T01:30:00Z"
                and rows[0].get("endTime") == "2030-01-02T02:30:00Z"
            )
            checks["one_credit_and_success"] = (
                after["credit_delta"] == -1
                and decision.get("execution_status") == "SUCCEEDED"
                and gateway.attempts == 1
            )
            return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    finally:
        gateway.db.close()


def supports(case: Any) -> bool:
    return case.case_id in _CASES and matches_frozen_case(case)


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("unsupported booking component fixture")
    started = time.monotonic()
    executed = False
    failure = None
    evidence: dict[str, Any] = {}
    checks: dict[str, bool] = {}
    if case.case_id in {"BOOK-001", "BOOK-002"}:
        try:
            result = (
                await _coach_time_probe(case.steps[0].message)
                if case.case_id == "BOOK-002"
                else await local.evaluate_case("approve")
            )
            executed = bool(result.get("checks", {}).get("chat_api_succeeded"))
            if result.get("status") == "ERROR":
                failure = "ERROR"
            else:
                checks = result.get("checks", {})
            evidence = {"checks": checks, "business": result.get("business", {})}
        except Exception:  # noqa: BLE001 - do not expose exception contents
            failure = "ERROR"
    else:
        repository = local.Repository()
        gateway = local.Gateway(repository, "approve")
        registry = build_fitness_tool_registry(cast(Any, gateway))
        cipher = AesGcmPayloadCipher(key=b"x" * 32, key_version="local")
        service = ConfirmationService(
            cast(Any, repository),
            registry,
            cast(Any, gateway),
            cipher,
            cast(Any, local.TokenIssuer()),
        )
        context = GatewayRequestContext(signed_context="synthetic-context", request_id="fixture")

        async def prepare(payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
            record = await service.prepare(
                tool_id="fitness.booking.create.v1",
                raw_input=payload,
                gateway_context=context,
                identity=local.IDENTITY,
                thread_id="synthetic-thread",
            )
            body = json.loads(
                cipher.decrypt(
                    record.payload_ciphertext,
                    associated_data=record.payload_hash,
                    key_version=record.payload_key_version,
                )
            )
            return record, body

        try:
            payload = dict(local.PAYLOAD)
            if case.case_id == "BOOK-003":
                record, body = await prepare(payload)
                other, other_body = await prepare({**payload, "course_id": "course-2"})
                executed = True
                try:
                    cipher.decrypt(record.payload_ciphertext, associated_data=other.payload_hash)
                    cross_binding_rejected = False
                except ConfirmationPayloadCipherError:
                    cross_binding_rejected = True
                checks = {
                    "course_in_confirmation_and_payload": (
                        body.get("courseId") == "course-1"
                        and record.display_summary.get("details", {}).get("courseId") == "course-1"
                    ),
                    "course_change_changes_bound_hash": (
                        other_body.get("courseId") == "course-2"
                        and record.payload_hash != other.payload_hash
                    ),
                    "course_hash_cannot_rebind_ciphertext": cross_binding_rejected,
                }
            elif case.case_id == "BOOK-005":
                record, body = await prepare(payload)
                other, other_body = await prepare({**payload, "coach_id": "coach-2"})
                executed = True
                checks = {
                    "coach_in_confirmation_and_payload": (
                        body.get("coachId") == "coach-1"
                        and record.display_summary.get("details", {}).get("coachId") == "coach-1"
                    ),
                    "coach_change_changes_bound_hash": (
                        other_body.get("coachId") == "coach-2"
                        and record.payload_hash != other.payload_hash
                    ),
                    "coach_change_does_not_change_course": (
                        body.get("courseId") == other_body.get("courseId") == "course-1"
                    ),
                }
            elif case.case_id == "BOOK-004":
                record, body = await prepare(payload)
                other, other_body = await prepare({**payload, "contract_id": "contract-2"})
                executed = True
                checks = {
                    "selected_contract_bound_to_confirmation": (
                        record.resource_id == "contract-1"
                        and body.get("contractId") == "contract-1"
                        and record.display_summary.get("details", {}).get("contractId")
                        == "contract-1"
                    ),
                    "changed_contract_changes_confirmation_hash": (
                        other.resource_id == "contract-2"
                        and other_body.get("contractId") == "contract-2"
                        and record.payload_hash != other.payload_hash
                    ),
                }
            elif case.case_id == "BOOK-006":
                # An organization is not a store; only tenant binding is observed.
                proposed = {
                    **payload,
                    "organization_id": "other-org",
                    "organizationId": "third-org",
                }
                bound = registry.bind_context_input(
                    "fitness.booking.create.v1", proposed, local.IDENTITY
                )
                executed = True
                checks = {
                    "organization_bound_to_signed_scope": bound.get("organization_id") == "org-1",
                    "conflicting_organization_alias_removed": "organizationId" not in bound,
                }
                if all(checks.values()):
                    record, body = await prepare(bound)
                    checks["confirmation_matches_signed_organization"] = (
                        record.organization_id == "org-1"
                        and body.get("organizationId") == "org-1"
                        and record.display_summary.get("organization_id") == "org-1"
                    )
            elif case.case_id == "BOOK-007":
                # The actual student binding must override conflicting model-supplied aliases.
                proposed = {**payload, "student_id": "other-student", "studentId": "third-student"}
                bound = registry.bind_context_input(
                    "fitness.booking.create.v1", proposed, local.IDENTITY
                )
                record, body = await prepare(bound)
                executed = True
                checks = {
                    "student_bound_to_signed_actor": (
                        bound.get("student_id") == local.IDENTITY.subject
                        and body.get("studentId") == local.IDENTITY.subject
                        and record.subject_user_id == local.IDENTITY.subject
                    ),
                    "conflicting_student_alias_removed": "studentId" not in bound,
                }
            else:
                payload.update(
                    start_time="2030-01-01T00:30:00+08:00", end_time="2030-01-01T01:30:00+08:00"
                )
                record, body = await prepare(payload)
                executed = True
                checks = {
                    "timezone_and_calendar_day_preserved": (
                        body.get("startTime") == "2029-12-31T16:30:00Z"
                        and body.get("endTime") == "2029-12-31T17:30:00Z"
                        and record.display_summary.get("details", {}).get("startTime")
                        == body.get("startTime")
                    ),
                    "confirmation_pending": record.authorization_status == "PENDING"
                    and record.execution_status == "NOT_STARTED",
                }
            snapshot = gateway.observe()
            checks["no_write_during_confirmation"] = (
                gateway.attempts == 0
                and snapshot["after"]["booking_count"] == 0
                and snapshot["credit_delta"] == 0
                and not snapshot["commits"]
            )
            evidence = {"checks": checks}
        except Exception:  # noqa: BLE001 - preserve sanitized failure only
            failure = "ERROR"
        finally:
            gateway.db.close()
    assertions = [
        {
            "id": a.id,
            "rule": a.rule,
            "phase": a.phase,
            "required": a.required,
            "risk": a.risk,
            "applicable": a.applicable,
            "status": "UNKNOWN" if a.applicable else "NA",
            "reason": "CASE_CONTEXT_AND_MODEL_NOT_VALIDATED" if a.applicable else "NOT_APPLICABLE",
        }
        for a in case.expected.assertions
    ]
    assertions.extend(
        {
            "id": "supplemental_" + name,
            "rule": "local_booking_case_component",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "PASS" if passed else "FAIL",
            "reason": "LOCAL_PROBE_MATCH" if passed else "LOCAL_PROBE_MISMATCH",
            "evidence_origin": "python_confirmation_and_local_business_test_double",
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
        "reason": "LOCAL_COMPONENT_ERROR" if failure else None,
        "assertions": assertions,
        "evidence": evidence,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "result_origin": "python_confirmation_and_local_business_test_double",
        "limitations": [
            "DRAFT_CASE",
            "SYNTHETIC_TOOL_INPUT",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "NO_REAL_MODEL_OR_CASE_CONVERSATION_EVALUATED",
            "CONTRACT_ELIGIBILITY_NOT_TESTED",
            "INDEPENDENT_DATABASE_AUDIT_NOT_TESTED",
        ]
        + (["STORE_IDENTIFIER_AND_SELECTION_NOT_TESTED"] if case.case_id == "BOOK-006" else []),
    }
