"""Case-bound confirmation input guards with an isolated business gateway.

BOOK-012/013/014 supply missing time/coach/course identifiers to the actual
confirmation service, respectively.
BOOK-015 supplies a literal relative time as invalid tool arguments; it does
not prove that an Agent will avoid inventing a specific time.
BOOK-016 supplies an unspecified course and an unresolved weekday-evening
literal together; it does not prove the Agent asked the user for a course.
BOOK-017 supplies synthetic local times to that service to check its documented
Asia/Shanghai normalization. Neither probe assesses the model's interpretation
of the user message or whether it asks an appropriate clarification.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import ToolConfirmationNormalizationError
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayRequestContext

from . import booking_local as local
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_CASES = {"BOOK-012", "BOOK-013", "BOOK-014", "BOOK-015", "BOOK-016", "BOOK-017"}


def supports(case: Any) -> bool:
    return case.case_id in _CASES and matches_frozen_case(case)


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("unsupported booking input boundary fixture")
    started = time.monotonic()
    repository = local.Repository()
    gateway = local.Gateway(repository, "approve")
    cipher = AesGcmPayloadCipher(key=b"x" * 32, key_version="local")
    service = ConfirmationService(
        cast(Any, repository),
        build_fitness_tool_registry(cast(Any, gateway)),
        cast(Any, gateway),
        cipher,
        cast(Any, local.TokenIssuer()),
    )
    payload = dict(local.PAYLOAD)
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    executed = False
    failure = None
    try:
        if case.case_id in {"BOOK-012", "BOOK-013", "BOOK-014"}:
            fields = {
                "BOOK-012": (("start_time", "end_time"), {"startTime", "endTime"}, "time"),
                "BOOK-013": (("coach_id",), {"coachId"}, "coach_identifier"),
                "BOOK-014": (("course_id",), {"courseId"}, "course_identifier"),
            }
            removed, expected_errors, label = fields[case.case_id]
            for name in removed:
                payload.pop(name)
            rejected = False
            missing_errors: set[str] = set()
            try:
                await service.prepare(
                    tool_id="fitness.booking.create.v1",
                    raw_input=payload,
                    gateway_context=GatewayRequestContext(signed_context="local-context"),
                    identity=local.IDENTITY,
                    thread_id="local-thread",
                )
            except ToolConfirmationNormalizationError as exc:
                rejected = True
                if isinstance(exc.__cause__, ValidationError):
                    missing_errors = {
                        str(error["loc"][0])
                        for error in exc.__cause__.errors(include_input=False)
                        if error.get("type") == "missing" and len(error.get("loc", ())) == 1
                    }
            executed = True
            checks[f"missing_{label}_rejected"] = rejected
            checks[f"missing_{label}_identified"] = missing_errors == expected_errors
            checks["no_confirmation_created"] = not hasattr(repository, "current")
        elif case.case_id in {"BOOK-015", "BOOK-016"}:
            relative = "下周" if case.case_id == "BOOK-015" else "周三晚上"
            payload["start_time"] = relative
            payload["end_time"] = relative
            if case.case_id == "BOOK-016":
                payload.pop("course_id")
            rejected = False
            invalid_errors: set[tuple[str, str]] = set()
            try:
                await service.prepare(
                    tool_id="fitness.booking.create.v1",
                    raw_input=payload,
                    gateway_context=GatewayRequestContext(signed_context="local-context"),
                    identity=local.IDENTITY,
                    thread_id="local-thread",
                )
            except ToolConfirmationNormalizationError as exc:
                rejected = True
                if isinstance(exc.__cause__, ValidationError):
                    invalid_errors = {
                        (str(error["loc"][0]), str(error["type"]))
                        for error in exc.__cause__.errors(include_input=False)
                        if len(error.get("loc", ())) == 1
                    }
            executed = True
            checks["relative_time_literal_rejected"] = rejected
            expected_validation_errors = {
                ("start_time", "datetime_from_date_parsing"),
                ("end_time", "datetime_from_date_parsing"),
            }
            if case.case_id == "BOOK-016":
                expected_validation_errors.add(("courseId", "missing"))
            checks[
                "missing_course_and_time_validation"
                if case.case_id == "BOOK-016"
                else "both_time_fields_identified_as_invalid"
            ] = invalid_errors == expected_validation_errors
            checks["no_confirmation_created"] = not hasattr(repository, "current")
        else:
            payload["start_time"] = "2030-01-07T09:00:00"
            payload["end_time"] = "2030-01-07T10:00:00"
            record = await service.prepare(
                tool_id="fitness.booking.create.v1",
                raw_input=payload,
                gateway_context=GatewayRequestContext(signed_context="local-context"),
                identity=local.IDENTITY,
                thread_id="local-thread",
            )
            executed = True
            clear = json.loads(
                cipher.decrypt(
                    record.payload_ciphertext,
                    associated_data=record.payload_hash,
                    key_version=record.payload_key_version,
                )
            )
            checks["local_time_normalized_to_china_timezone"] = (
                clear.get("startTime") == "2030-01-07T01:00:00Z"
                and clear.get("endTime") == "2030-01-07T02:00:00Z"
            )
            checks["confirmation_still_pending"] = (
                record.authorization_status == "PENDING"
                and record.execution_status == "NOT_STARTED"
            )
            evidence["normalized_start"] = clear.get("startTime")
            evidence["normalized_end"] = clear.get("endTime")
        snapshot = gateway.observe()
        checks["no_business_write"] = (
            gateway.attempts == 0
            and snapshot["after"]["booking_count"] == 0
            and snapshot["credit_delta"] == 0
            and not snapshot["commits"]
        )
        evidence["checks"] = checks
    except Exception:  # noqa: BLE001 - avoid exposing arbitrary error text
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
            "reason": "MESSAGE_INTERPRETATION_NOT_TESTED" if a.applicable else "NOT_APPLICABLE",
        }
        for a in case.expected.assertions
    ]
    assertions.extend(
        {
            "id": "supplemental_" + name,
            "rule": "confirmation_input_boundary",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "PASS" if passed else "FAIL",
            "reason": "LOCAL_PROBE_MATCH" if passed else "LOCAL_PROBE_MISMATCH",
            "evidence_origin": "real_confirmation_service_with_local_business_test_double",
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
        "result_origin": "real_confirmation_service_with_local_business_test_double",
        "limitations": [
            "DRAFT_CASE",
            "NO_MODEL_OR_CONVERSATION_EXECUTED",
            "SYNTHETIC_TOOL_INPUT",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "CLARIFICATION_AND_CASE_CONTEXT_NOT_TESTED",
        ],
    }
