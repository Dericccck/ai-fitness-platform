"""Synthetic BOOK-008 multi-turn protocol probe, not a model or release evaluation.

The two options are scripted, not retrieved from the business service; this
cannot establish availability, model interpretation or test-set acceptance.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import BookingCreateToolInput, build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from . import booking_local as local
from .agent_http import AgentHTTP
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_FIXTURE = Path(__file__).resolve().parents[3] / "evals/task_eval/dev_fixtures/book-008-v1.json"
_FIXTURE_SHA256 = "af8d99889743e2ab2fd8d31ed7755103120901dff842b761f3fd52c7393300f2"


def supports(case: Any) -> bool:
    return case.case_id == "BOOK-008" and case.split == "test" and matches_frozen_case(case)


def load(case: Any) -> dict[str, Any]:
    if not supports(case) or case.review.status != "draft":
        raise ValueError("CASE_CHANGED")
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _FIXTURE_SHA256:
        raise ValueError("FIXTURE_CHANGED")
    fixture = json.loads(raw)
    first, second = fixture["first_payload"], fixture["second_payload"]
    BookingCreateToolInput.model_validate(first)
    BookingCreateToolInput.model_validate(second)
    if (
        fixture["version"] != "1.0"
        or fixture["status"] != "synthetic_test_draft_not_release_ready"
        or fixture["case_id"] != case.case_id
        or fixture["selected_message"] != case.steps[0].message
        or first != local.PAYLOAD
        or first["course_id"] == second["course_id"]
        or first["coach_id"] == second["coach_id"]
    ):
        raise ValueError("FIXTURE_INCONSISTENT")
    return cast(dict[str, Any], fixture)


class CandidateModel:
    """Fixed output intentionally exposes only protocol/session behavior."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.calls = 0
        self.context_preserved = False

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(content=self.fixture["candidate_message"], tool_calls=())
        if self.calls == 2:
            self.context_preserved = any(
                m.get("role") == "assistant"
                and m.get("content") == self.fixture["candidate_message"]
                for m in messages
            ) and any(
                m.get("role") == "user" and m.get("content") == self.fixture["selected_message"]
                for m in messages
            )
            return ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="select-first",
                        name="fitness_booking_create_v1",
                        arguments=self.fixture["first_payload"],
                    ),
                ),
            )
        return ModelTurn(content="请求已处理。", tool_calls=())


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    fixture = load(case)
    started = time.monotonic()
    repository = local.Repository()
    gateway = local.Gateway(repository, "approve")
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    model = CandidateModel(fixture)
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
    error = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            first = await agent.chat(fixture["initial_message"])
            checks["candidate_response"] = first.get("answer") == fixture["candidate_message"]
            checks["no_write_after_candidates"] = (
                gateway.attempts == 0 and gateway.observe()["credit_delta"] == 0
            )
            response = await agent.chat(fixture["selected_message"])
            executed = True
            cid = response.get("confirmation_id")
            checks["conversation_context_preserved"] = model.context_preserved
            checks["confirmation_required"] = (
                response.get("status") == "CONFIRMATION_REQUIRED"
                and isinstance(cid, str)
                and bool(cid)
            )
            before = gateway.observe()
            checks["no_write_before_decision"] = (
                gateway.attempts == 0
                and not before["created_bookings"]
                and before["credit_delta"] == 0
            )
            if checks["confirmation_required"] and isinstance(cid, str):
                decision = await agent.decide(cid, "APPROVE")
                snapshot = gateway.observe()
                checks["approved_and_succeeded"] = (
                    decision.get("authorization_status") == "APPROVED"
                    and decision.get("execution_status") == "SUCCEEDED"
                )
                checks["exactly_first_candidate_and_one_credit"] = (
                    snapshot["created_bookings"]
                    == [
                        {
                            "organizationId": "org-1",
                            "studentId": "student-1",
                            "contractId": "contract-1",
                            "coachId": "coach-1",
                            "courseId": "course-1",
                            "startTime": "2030-01-01T02:00:00Z",
                            "endTime": "2030-01-01T03:00:00Z",
                        }
                    ]
                    and snapshot["credit_delta"] == -1
                    and gateway.attempts == 1
                )
    except Exception:  # noqa: BLE001 - do not expose payloads or exception details
        error = "ERROR"
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
            "reason": "SCRIPTED_CANDIDATES_AND_MODEL_NOT_VALIDATED"
            if a.applicable
            else "NOT_APPLICABLE",
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
        "status": reduce_trial(assertions, executed=executed, failure=error),
        "reason": "LOCAL_COMPONENT_ERROR" if error else None,
        "assertions": assertions,
        "evidence": {"checks": checks, "fixture_sha256": _FIXTURE_SHA256},
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "result_origin": "synthetic_candidate_and_scripted_model_with_local_business_double",
        "limitations": [
            "DRAFT_CASE",
            "SCRIPTED_CANDIDATES",
            "SCRIPTED_MODEL",
            "BUSINESS_SERVICE_TEST_DOUBLE",
            "MODEL_CHOICE_AND_AVAILABILITY_NOT_EVALUATED",
            "NOT_RELEASE_ELIGIBLE",
        ],
    }
