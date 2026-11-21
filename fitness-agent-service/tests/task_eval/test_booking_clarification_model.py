import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor, SupervisorRequest
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_local as local
from app.evaluation.task_eval.booking_clarification_model import (
    ClarificationProbeError,
    OneClarificationModel,
)
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dev_sandbox_book_clarification import _net_status
from app.infrastructure.gateway_client import GatewayRequestContext


def ledger(path: Path) -> DurableBudget:
    return DurableBudget(path, limit_nano=10_000_000_000, opening_nano=46_080_000)


def response(arguments: str = "{}") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "fitness_booking_create_v1",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        },
    }


@pytest.mark.asyncio
async def test_actual_supervisor_forces_tool_and_clarifies_missing_parameters(tmp_path):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    model = OneClarificationModel(
        "BOOK-011",
        "synthetic-credential-123",
        ledger(tmp_path / "budget"),
        transport=httpx.MockTransport(provider),
    )
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
    supervisor = Supervisor(
        cast(Any, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    try:
        result = await supervisor.invoke(
            SupervisorRequest(
                user_message="帮我约一节私教课",
                gateway_context=GatewayRequestContext(signed_context="signed-context"),
                conversation_id="synthetic",
                identity=local.IDENTITY,
            )
        )
    finally:
        gateway.db.close()
    assert requests[0]["tool_choice"]["function"]["name"] == "fitness_booking_create_v1"
    assert model.reason == "MODEL_RESPONSE_RECEIVED"
    assert model.last_arguments == {}
    assert result.status == "CLARIFICATION_REQUIRED"
    assert result.confirmation_id is None


@pytest.mark.asyncio
async def test_input_drift_fails_before_network_or_reservation(tmp_path):
    budget = ledger(tmp_path / "budget")
    model = OneClarificationModel(
        "BOOK-011",
        "synthetic-credential-123",
        budget,
        transport=httpx.MockTransport(lambda _: pytest.fail("network must not be used")),
    )
    with pytest.raises(ClarificationProbeError):
        await model.chat_with_tools([{"role": "user", "content": "different"}], tools=[])
    assert model.attempts == 0 and budget.snapshot()["pending"] == 0


def test_missing_business_snapshot_is_unknown_instead_of_failure():
    assert _net_status({}, {}) == "UNKNOWN"


@pytest.mark.asyncio
async def test_invented_past_time_cannot_create_a_confirmation(tmp_path):
    fabricated = json.dumps(
        {
            "studentId": "placeholder",
            "contractId": "placeholder",
            "coachId": "placeholder",
            "courseId": "placeholder",
            "startTime": "2025-01-01T00:00:00Z",
            "endTime": "2025-01-01T01:00:00Z",
        }
    )
    model = OneClarificationModel(
        "BOOK-015",
        "synthetic-credential-123",
        ledger(tmp_path / "budget"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(fabricated))),
    )
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
    supervisor = Supervisor(
        cast(Any, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    try:
        result = await supervisor.invoke(
            SupervisorRequest(
                user_message="下周找个合适的时间约课",
                gateway_context=GatewayRequestContext(signed_context="signed-context"),
                conversation_id="synthetic",
                identity=local.IDENTITY,
            )
        )
    finally:
        gateway.db.close()
    assert result.status == "CLARIFICATION_REQUIRED"
    assert result.confirmation_id is None
    assert not hasattr(repository, "current")
