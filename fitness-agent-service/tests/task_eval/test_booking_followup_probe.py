from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.routing import HybridIntentRouter
from app.agent.supervisor import Supervisor, _model_tools, _system_prompt
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_local as local
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_followup_probe import (
    FOLLOWUP,
    FollowupModel,
    _approved_read_arguments,
)
from app.evaluation.task_eval.booking_model_boundary import PROMPT, OneBookingModel
from app.evaluation.task_eval.budget_store import DurableBudget
from app.infrastructure.model_gateway import ModelResponseError
from tests.task_eval.test_booking_model_boundary import response

KEY = "synthetic-evaluation-credential"
BOOKING = "a" * 32


def booking(created: bool = True) -> dict[str, Any]:
    data = {
        "id": BOOKING,
        "organizationId": "org-1",
        "userId": "student-1",
        "coachId": "coach-1",
        "courseId": "course-1",
        "courseName": "test course",
        "startTime": "2030-01-07T01:00:00Z",
        "endTime": "2030-01-07T02:00:00Z",
        "status": 1,
        "contractId": "contract-1",
    }
    if created:
        data["remainingClassHours"] = 1
    return data


def provider(
    *,
    tool: bool = False,
    name: str = "fitness_appointment_list_v1",
    args: Any = None,
    content: str = "合成答复",
) -> dict[str, Any]:
    message: dict[str, Any] = {"content": None if tool else content}
    if tool:
        message["tool_calls"] = [
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args or {})},
            }
        ]
    return {
        "choices": [{"finish_reason": "tool_calls" if tool else "stop", "message": message}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        },
    }


def setup_model(tmp_path: Path, handler: Any) -> tuple[FollowupModel, DurableBudget]:
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    return FollowupModel(
        KEY,
        budget,
        BOOKING,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
    ), budget


def messages() -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": _system_prompt("CUSTOMER_SERVICE", "zh-CN", intent="QUERY_APPOINTMENT"),
        },
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old-call",
                    "type": "function",
                    "function": {"name": "fitness_booking_create_v1", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "old-call",
            "content": json.dumps(booking(), ensure_ascii=False),
        },
        {"role": "assistant", "content": "已完成创建健身预约。"},
        {"role": "user", "content": FOLLOWUP},
    ]


def kwargs() -> dict[str, Any]:
    return {
        "tools": _model_tools(
            build_fitness_tool_registry(cast(Any, object())),
            "CUSTOMER_SERVICE",
            "QUERY_APPOINTMENT",
        )
    }


async def test_direct_response_and_duplicate_call_stops(tmp_path: Path) -> None:
    seen = []
    model, budget = setup_model(
        tmp_path, lambda request: (seen.append(request), httpx.Response(200, json=provider()))[1]
    )
    turn = await model.chat_with_tools(messages(), **kwargs())
    assert turn.content and model.observation()["status"] == "DIRECT_ANSWER"
    assert (
        model.observation()["output_sha256"]
        and not model.observation()["raw_output_human_reviewable"]
    )
    assert "合成答复" not in json.dumps(model.observation(), ensure_ascii=False)
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools(messages(), **kwargs())
    assert len(seen) == 1 and budget.snapshot()["reserved_nano"] == 102_400_000


@pytest.mark.parametrize("change", ["extra_result", "other_user", "extra_history", "schema"])
async def test_input_out_of_scope_never_sent(tmp_path: Path, change: str) -> None:
    seen = []
    model, budget = setup_model(tmp_path, lambda request: seen.append(request))
    data = messages()
    tools = kwargs()
    if change in ("extra_result", "other_user"):
        result = booking()
        if change == "extra_result":
            result["private"] = "do not send"
        else:
            result["userId"] = "someone-else"
        data[3]["content"] = json.dumps(result)
    elif change == "extra_history":
        data.append({"role": "user", "content": "unreviewed"})
    else:
        tools["tools"].clear()
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools(data, **tools)
    assert not seen and budget.snapshot()["reserved_nano"] == 0


@pytest.mark.parametrize(
    "name,args,reason",
    [
        ("fitness_booking_create_v1", {}, "CALL_STRUCTURE_OR_NAME_INVALID"),
        ("fitness_appointment_list_v1", {"user_id": "other"}, "USER_OUTSIDE_FIXTURE"),
        ("fitness_appointment_list_v1", {"limit": 101}, "LIMIT_OUTSIDE_TOOL_CONTRACT"),
        ("fitness_appointment_list_v1", {"limit": True}, "LIMIT_OUTSIDE_TOOL_CONTRACT"),
        ("fitness_appointment_list_v1", {"private-value": "marker"}, "UNEXPECTED_ARGUMENT_FIELD"),
    ],
)
async def test_model_cannot_execute_unapproved_tool(
    tmp_path: Path, name: str, args: Any, reason: str
) -> None:
    model, budget = setup_model(
        tmp_path, lambda _: httpx.Response(200, json=provider(tool=True, name=name, args=args))
    )
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools(messages(), **kwargs())
    assert model.status == "UNAPPROVED_TOOL_REQUEST" and not budget.snapshot()["frozen"]
    assert not model.requested_read
    assert model.observation()["tool_validation_reason"] == reason
    assert "private-value" not in json.dumps(model.observation())


async def test_timeout_freezes_budget(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret error", request=request)

    model, budget = setup_model(tmp_path, handler)
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools(messages(), **kwargs())
    assert budget.snapshot()["frozen"] and not budget.snapshot()["pending"]
    assert "secret" not in json.dumps(model.observation())


@pytest.mark.parametrize("change", ["foreign_result", "extra_row", "changed_call"])
async def test_second_turn_rejects_untrusted_tool_result_before_network(
    tmp_path: Path, change: str
) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=provider(tool=True))

    model, budget = setup_model(tmp_path, handler)
    first = messages()
    turn = await model.chat_with_tools(first, **kwargs())
    call = turn.tool_calls[0]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
        ],
    }
    result = booking(False)
    if change == "foreign_result":
        result["organizationId"] = "org-2"
    items = [result, result] if change == "extra_row" else [result]
    if change == "changed_call":
        assistant["tool_calls"][0]["id"] = "different"
    tool = {"role": "tool", "tool_call_id": call.call_id, "content": json.dumps(items)}
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools([*first, assistant, tool], **kwargs())
    assert len(seen) == 1 and budget.snapshot()["reserved_nano"] == 102_400_000
    assert (
        model.observation()["input_validation_reason"]
        == {
            "foreign_result": "READ_BOOKING_MISMATCH",
            "extra_row": "READ_RESULT_SHAPE_MISMATCH",
            "changed_call": "READ_CALL_MISMATCH",
        }[change]
    )


@pytest.mark.parametrize(
    "arguments,valid",
    [
        ({}, True),
        ({"limit": 100}, True),
        ({"limit": 101}, False),
        ({"from_time": "2030-01-06T00:00:00+08:00", "to_time": "2030-01-08T00:00:00+08:00"}, True),
        ({"from_time": "2030-01-06T16:00:00Z", "to_time": "2030-01-07T16:00:00Z"}, True),
        ({"from_time": "2030-01-06T00:00:00+08:00"}, False),
        ({"from_time": "2020-01-01T00:00:00+08:00", "to_time": "2030-01-08T00:00:00+08:00"}, False),
        ({"from_time": "2030-01-07T10:00:00+08:00", "to_time": "2030-01-08T00:00:00+08:00"}, False),
        ({"from_time": "2030-01-06T00:00:00", "to_time": "2030-01-08T00:00:00"}, False),
        ({"from_time": "2030-01-06T00:00:00+08:00", "to_time": "2030-01-10T00:00:00+08:00"}, False),
    ],
)
def test_approved_read_window_covers_only_the_synthetic_fixture(arguments, valid):
    assert _approved_read_arguments(arguments) is valid


async def test_empty_read_is_retained_as_empty_and_never_marked_success(tmp_path: Path) -> None:
    replies = iter([provider(tool=True), provider(content="该查询窗口没有预约")])
    model, budget = setup_model(tmp_path, lambda _: httpx.Response(200, json=next(replies)))
    first = messages()
    call = (await model.chat_with_tools(first, **kwargs())).tool_calls[0]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
        ],
    }
    tool = {"role": "tool", "tool_call_id": call.call_id, "content": "[]"}
    await model.chat_with_tools([*first, assistant, tool], **kwargs())
    observed = model.observation()
    assert observed["status"] == "ANSWER_AFTER_EMPTY_READ"
    assert observed["read_outcome"] == "EMPTY" and not observed["read_result_validated"]
    assert observed["component_status"] == "INCOMPLETE" and observed["task_status"] == "UNKNOWN"
    assert budget.snapshot()["reserved_nano"] == 204_800_000


async def test_real_supervisor_same_session_and_real_registered_read_tool(tmp_path: Path) -> None:
    class Gateway(local.Gateway):
        def __init__(self, repository: Any) -> None:
            super().__init__(repository, "approve")
            self.reads = 0

        async def create_booking(self, context: Any, payload: Any) -> Any:
            await super().create_booking(context, payload)
            return booking()

        async def list_appointments(
            self, context: Any, organization_id: str, **parameters: Any
        ) -> Any:
            self.reads += 1
            assert organization_id == "org-1" and parameters["user_id"] in (None, "student-1")
            assert parameters["limit"] == 100
            assert parameters["from_time"] == datetime.fromisoformat("2030-01-06T00:00:00+08:00")
            assert parameters["to_time"] == datetime.fromisoformat("2030-01-08T00:00:00+08:00")
            return [booking(False)]

    repository = local.Repository()
    gateway = Gateway(repository)
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    first = OneBookingModel(
        KEY,
        budget,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response())),
    )
    supervisor = Supervisor(
        cast(Any, first),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = supervisor
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    replies = iter(
        [
            provider(
                tool=True,
                args={
                    "from_time": "2030-01-06T00:00:00+08:00",
                    "to_time": "2030-01-08T00:00:00+08:00",
                    "limit": 100,
                },
            ),
            provider(content="查询到了合成预约"),
        ]
    )
    followup = FollowupModel(
        KEY,
        budget,
        BOOKING,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(replies))),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as http:
        agent = AgentHTTP(http, "signed-context")
        result = await agent.chat(PROMPT)
        await agent.decide(result["confirmation_id"], "APPROVE")
        supervisor.models = cast(Any, followup)
        supervisor.intent_router = HybridIntentRouter(cast(Any, followup))
        result = await agent.chat(FOLLOWUP)
    gateway.db.close()
    assert result["route"] == "CUSTOMER_SERVICE" and result["tool_steps"] == 1
    assert gateway.attempts == gateway.reads == 1
    assert followup.observation()["status"] == "ANSWER_AFTER_READ" and followup.read_validated
    assert followup.observation()["read_outcome"] == "MATCHED_FIXTURE"
    assert budget.snapshot()["reserved_nano"] == 409_600_000
