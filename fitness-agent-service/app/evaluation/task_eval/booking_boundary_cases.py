"""Case-bound confirmation input guards with an isolated business gateway.

BOOK-013 supplies no coach identifier to the actual confirmation service.
BOOK-017 supplies synthetic local times to that service to check its documented
Asia/Shanghai normalization. Neither probe assesses the model's interpretation
of the user message or whether it asks an appropriate clarification.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast
from uuid import uuid4

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import ToolConfirmationNormalizationError
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayRequestContext

from . import booking_local as local
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_CASES = {"BOOK-013", "BOOK-017"}


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
        if case.case_id == "BOOK-013":
            payload.pop("coach_id")
            rejected = False
            try:
                await service.prepare(
                    tool_id="fitness.booking.create.v1",
                    raw_input=payload,
                    gateway_context=GatewayRequestContext(signed_context="local-context"),
                    identity=local.IDENTITY,
                    thread_id="local-thread",
                )
            except ToolConfirmationNormalizationError:
                rejected = True
            executed = True
            checks["missing_coach_identifier_rejected"] = rejected
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
