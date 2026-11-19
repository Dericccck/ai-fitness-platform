"""BOOK-011 draft probes; do not grade model reasoning or store selection.

The business tool has no store identifier.  These probes establish only that
missing booking identifiers fail validation and that a scripted clarification
can traverse the real Supervisor without calling the local gateway.
"""

from __future__ import annotations

import time
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.agent.tool_registry import ToolConfirmationNormalizationError
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelTurn

from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .report import reduce_trial


class _ClarifyingModel:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        self.calls += 1
        return ModelTurn(content="请问您想在哪家门店上私教课？", tool_calls=())


def supports(case: Any) -> bool:
    return (
        case.case_id == "BOOK-011"
        and case.split == "dev"
        and case.review.status == "draft"
        and matches_frozen_case(case)
    )


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("CASE_CHANGED")
    start = time.monotonic()
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
    model = _ClarifyingModel()
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = Supervisor(
        cast(Any, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    checks: dict[str, bool] = {}
    executed = False
    failure = None
    try:
        rejected = False
        missing: set[str] = set()
        try:
            await service.prepare(
                tool_id="fitness.booking.create.v1",
                raw_input={},
                gateway_context=GatewayRequestContext(signed_context="local-context"),
                identity=local.IDENTITY,
                thread_id="local-thread",
            )
        except ToolConfirmationNormalizationError as exc:
            rejected = True
            if isinstance(exc.__cause__, ValidationError):
                missing = {
                    str(item["loc"][0])
                    for item in exc.__cause__.errors(include_input=False)
                    if item.get("type") == "missing" and len(item.get("loc", ())) == 1
                }
        checks["empty_booking_rejected"] = rejected
        checks["missing_booking_fields_identified"] = {
            "contractId",
            "coachId",
            "courseId",
            "startTime",
            "endTime",
        }.issubset(missing)
        checks["invalid_input_created_no_confirmation"] = not hasattr(repo, "current")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            response = await agent.chat(case.steps[0].message)
        executed = True
        checks["scripted_clarification_delivered"] = (
            model.calls == 1
            and response.get("answer") == "请问您想在哪家门店上私教课？"
            and response.get("status", "COMPLETED") == "COMPLETED"
            and response.get("tool_steps") == 0
        )
        checks["no_confirmation"] = not hasattr(repo, "current") and not response.get(
            "confirmation_id"
        )
        observed = gateway.observe()
        checks["no_local_booking_or_credit_change"] = (
            gateway.attempts == 0
            and not observed["created_bookings"]
            and observed["credit_delta"] == 0
            and not observed["commits"]
        )
    except Exception:  # noqa: BLE001 - retain a sanitized error only
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
            "reason": "STORE_CONTRACT_AND_MODEL_NOT_VALIDATED"
            if a.applicable
            else "NOT_APPLICABLE",
        }
        for a in case.expected.assertions
    ]
    assertions.extend(
        {
            "id": "supplemental_" + name,
            "rule": "local_booking_boundary_component",
            "phase": "final",
            "required": True,
            "risk": None,
            "applicable": True,
            "status": "PASS" if passed else "FAIL",
            "reason": "LOCAL_PROBE_MATCH" if passed else "LOCAL_PROBE_MISMATCH",
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
        "evidence": {"checks": checks},
        "duration_ms": round((time.monotonic() - start) * 1000, 3),
        "result_origin": "scripted_clarification_and_local_business_double",
        "limitations": [
            "DRAFT_CASE",
            "SCRIPTED_MODEL",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "STORE_SELECTION_CONTRACT_UNAVAILABLE",
            "MODEL_CLARIFICATION_NOT_EVALUATED",
            "INDEPENDENT_AUDIT_NOT_TESTED",
            "NOT_RELEASE_ELIGIBLE",
        ],
    }
