"""Distinct case-bound booking components, using only synthetic inputs.

These tests do not assess the case messages or establish contract eligibility,
real-model quality, or Java gateway results. No live model is constructed.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast
from uuid import uuid4

from app.agent.fitness_tools import build_fitness_tool_registry
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayRequestContext

from . import booking_local as local
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_CASES = {"BOOK-001", "BOOK-004", "BOOK-007", "BOOK-010"}


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
    if case.case_id == "BOOK-001":
        try:
            result = await local.evaluate_case("approve")
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
            if case.case_id == "BOOK-004":
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
        ],
    }
