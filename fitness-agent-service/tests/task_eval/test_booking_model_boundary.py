from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_local as local
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_model_boundary import PROMPT, OneBookingModel
from app.evaluation.task_eval.budget_store import DurableBudget


def budget(path: Path) -> DurableBudget:
    ledger = DurableBudget(path, limit_nano=10_000_000_000, opening_nano=46_080_000)
    ledger.reserve("earlier", 102_400_000)
    ledger.finish("earlier", usage_known=True)
    return ledger


def response(*, coach: str = "coach-1", usage: Any = "valid") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "model-call-1",
                            "type": "function",
                            "function": {
                                "name": "fitness_booking_create_v1",
                                "arguments": json.dumps(
                                    {
                                        "studentId": "student-1",
                                        "contractId": "contract-1",
                                        "coachId": coach,
                                        "courseId": "course-1",
                                        "startTime": "2030-01-07T09:00:00+08:00",
                                        "endTime": "2030-01-07T10:00:00+08:00",
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 80,
            "total_tokens": 180,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        }
        if usage == "valid"
        else usage,
    }


async def run_local(
    model: OneBookingModel, *, decision: str = "REJECT", prompt: str = PROMPT
) -> dict[str, Any]:
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
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = supervisor
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://local"
        ) as client:
            agent = AgentHTTP(client, "signed-context")
            try:
                chat = await agent.chat(prompt)
            except httpx.HTTPStatusError:
                return {
                    "error": True,
                    "gateway_attempts": gateway.attempts,
                    "snapshot": gateway.observe(),
                }
            before = gateway.observe()
            scripted_followup = local.Model()
            scripted_followup.calls = 1
            supervisor.models = cast(Any, scripted_followup)
            result = await agent.decide(chat["confirmation_id"], cast(Any, decision))
            return {
                "chat": chat,
                "before": before,
                "result": result,
                "snapshot": gateway.observe(),
                "gateway_attempts": gateway.attempts,
            }
    finally:
        gateway.db.close()


@pytest.mark.parametrize("decision", ["REJECT", "APPROVE"])
async def test_single_mock_model_call_and_confirmation(decision: str, tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "https://api.deepseek.com/v1/chat/completions"
        assert ledger.snapshot()["pending"] == 1
        body = json.loads(request.content)
        assert body["max_tokens"] == 512 and body["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json=response())

    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(handler),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model, decision=decision)
    assert len(requests) == 1 and model.ledger.attempts == 1
    assert result["chat"]["status"] == "CONFIRMATION_REQUIRED"
    assert result["before"]["after"]["booking_count"] == 0
    assert result["gateway_attempts"] == (0 if decision == "REJECT" else 1)
    assert result["snapshot"]["after"]["booking_count"] == (0 if decision == "REJECT" else 1)
    assert result["snapshot"]["credit_delta"] == (0 if decision == "REJECT" else -1)
    assert ledger.snapshot()["reserved_nano"] == 353_280_000
    assert ledger.snapshot()["pending"] == 0


async def test_bad_model_booking_is_not_confirmed_or_written(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(coach="other"))),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model)
    assert result["error"] and result["gateway_attempts"] == 0
    assert model.reason == "FIXTURE_PARAMETERS_MISMATCH"
    assert ledger.snapshot()["pending"] == 0
    assert ledger.snapshot()["frozen"]
    assert ledger.audit()[-1]["details"]["reason_code"] == "FIXTURE_PARAMETERS_MISMATCH"


async def test_unexpected_provider_tool_freezes_even_with_valid_usage(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    result_body = response()
    result_body["choices"][0]["message"]["server_tool_calls"] = [{"type": "external"}]
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result_body)),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model)
    assert result["error"] and result["gateway_attempts"] == 0
    assert model.reason == "UNEXPECTED_REASONING_OR_SERVER_TOOL"
    assert ledger.snapshot()["frozen"] and ledger.snapshot()["pending"] == 0


async def test_changed_input_stops_before_reservation_and_network(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    calls = []
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model, prompt=PROMPT + "额外内容")
    assert result["error"] and result["gateway_attempts"] == 0 and not calls
    assert model.reason == "UNAPPROVED_MODEL_INPUT_OR_TOOLS"
    assert ledger.snapshot()["reserved_nano"] == 148_480_000


async def test_unknown_usage_freezes_budget_and_blocks_booking(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(usage=None))),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model)
    assert result["error"] and result["gateway_attempts"] == 0
    assert ledger.snapshot()["frozen"] and ledger.snapshot()["pending"] == 0
    assert model.reason == "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"


async def test_second_provider_request_is_blocked(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response())

    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(handler),
        execute=True,
        confirmed_current_pricing=True,
    )
    assert (await run_local(model))["chat"]["status"] == "CONFIRMATION_REQUIRED"
    assert (await run_local(model))["error"]
    assert model.reason == "CALL_LIMIT_OR_BUDGET_EXHAUSTED"
    assert len(seen) == 1
    assert ledger.snapshot()["reserved_nano"] == 353_280_000


async def test_persistent_budget_exhaustion_stops_before_network(tmp_path: Path) -> None:
    ledger = DurableBudget(
        tmp_path / "budget.sqlite", limit_nano=250_000_000, opening_nano=148_480_000
    )
    seen = []
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda req: seen.append(req)),
        execute=True,
        confirmed_current_pricing=True,
    )
    assert (await run_local(model))["error"]
    assert model.reason == "DURABLE_BUDGET_UNAVAILABLE"
    assert not seen and ledger.snapshot()["reserved_nano"] == 148_480_000


async def test_provider_redirect_never_followed_and_freezes_budget(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example/collect"})

    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(handler),
        execute=True,
        confirmed_current_pricing=True,
    )
    assert (await run_local(model))["error"]
    assert seen == ["https://api.deepseek.com/v1/chat/completions"]
    assert ledger.snapshot()["frozen"]


@pytest.mark.parametrize(
    ("scenario", "expected_reason", "expected_http", "expected_tokens"),
    [
        ("parameters", "FIXTURE_PARAMETERS_MISMATCH", 200, (100, 80)),
        ("server_tool", "UNEXPECTED_REASONING_OR_SERVER_TOOL", 200, (100, 80)),
        ("timeout", "MODEL_TIMEOUT", None, (None, None)),
    ],
)
def test_failure_report_is_whitelisted_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    scenario: str,
    expected_reason: str,
    expected_http: int | None,
    expected_tokens: tuple[int | None, int | None],
) -> None:
    from app.evaluation.task_eval import cross_service_booking as chain
    from app.evaluation.task_eval.booking_model_boundary import BookingModelProbeError

    ledger = budget(tmp_path / "budget.sqlite")
    secret_marker = "sensitive-provider-text-never-export"

    def handler(request: httpx.Request) -> httpx.Response:
        if scenario == "timeout":
            raise httpx.ReadTimeout(secret_marker, request=request)
        payload = response(coach="wrong-coach" if scenario == "parameters" else "coach-1")
        payload["untrusted_extra"] = secret_marker
        if scenario == "server_tool":
            payload["choices"][0]["message"]["server_tool_calls"] = [{"type": "external"}]
        return httpx.Response(200, json=payload)

    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(handler),
        execute=True,
        confirmed_current_pricing=True,
    )

    async def fake_evaluate(**_: Any) -> dict[str, Any]:
        outcome = await run_local(model)
        assert outcome["error"]
        raise BookingModelProbeError(secret_marker)

    monkeypatch.setattr(chain, "_mock_model", lambda _: model)
    monkeypatch.setattr(chain, "evaluate", fake_evaluate)
    monkeypatch.setattr("sys.argv", ["cross_service_booking", "--mock-model"])
    assert chain.main() == 1
    captured = capsys.readouterr()
    assert secret_marker not in captured.out + captured.err
    assert "mock-credential-for-tests" not in captured.out + captured.err
    report = json.loads(captured.out.splitlines()[-1])
    observed = report["model_observation"]
    assert observed["reason"] == expected_reason
    assert observed["http_status"] == expected_http
    assert (
        observed["reported_input_tokens"],
        observed["reported_output_tokens"],
    ) == expected_tokens
    assert observed["budget"]["frozen"] and observed["budget"]["pending"] == 0
    assert ledger.audit()[-1]["details"]["reason_code"] == expected_reason
    assert secret_marker not in json.dumps(ledger.audit())
    assert "mock-credential-for-tests" not in json.dumps(ledger.audit())
    assert not observed["provider_cap_verified"]


def test_unknown_reason_cannot_escape_whitelist(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        execute=True,
        confirmed_current_pricing=True,
    )
    model.reason = "provider-output-or-secret"
    result = model.diagnostics()
    assert result["reason"] == "UNCLASSIFIED_MODEL_FAILURE"
    assert "provider-output-or-secret" not in str(result)


@pytest.mark.parametrize(
    "scenario", ["missing_and_extra", "malformed", "different", "length", "optional_mark"]
)
async def test_model_diagnostics_describe_structure_without_provider_text(
    tmp_path: Path, scenario: str
) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    sensitive = "untrusted-extra-key-and-value"
    body = response(coach="unexpected-coach" if scenario == "different" else "coach-1")
    choice = body["choices"][0]
    if scenario == "length":
        choice["finish_reason"] = "length"
        choice["message"]["tool_calls"] = []
    else:
        function = choice["message"]["tool_calls"][0]["function"]
        if scenario == "malformed":
            function["arguments"] = "{" + sensitive
        elif scenario == "missing_and_extra":
            arguments = json.loads(function["arguments"])
            del arguments["studentId"]
            arguments[sensitive] = sensitive
            function["arguments"] = json.dumps(arguments)
        elif scenario == "optional_mark":
            arguments = json.loads(function["arguments"])
            arguments["mark"] = 1
            function["arguments"] = json.dumps(arguments)
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model)
    assert result["error"] and result["gateway_attempts"] == 0
    observed = model.diagnostics()
    assert sensitive not in json.dumps(observed)
    assert "unexpected-coach" not in json.dumps(observed)
    if scenario == "missing_and_extra":
        assert observed["reason"] == "INVALID_TOOL_ARGUMENTS"
        assert observed["argument_missing"] == ["studentId"]
        assert observed["argument_extra_count"] == 1
    elif scenario == "malformed":
        assert observed["reason"] == "INVALID_TOOL_ARGUMENTS"
        assert observed["argument_missing"] is None
    elif scenario == "different":
        assert observed["reason"] == "FIXTURE_PARAMETERS_MISMATCH"
        assert observed["argument_differing"] == ["coachId"]
    elif scenario == "optional_mark":
        assert observed["reason"] == "FIXTURE_PARAMETERS_MISMATCH"
        assert observed["argument_mark_state"] == "ALLOWED_INTEGER"
        assert observed["argument_differing"] == ["mark"]
        assert observed["argument_extra_count"] == 0
    else:
        assert observed["reason"] == "MISSING_TOOL_CALL"
        assert observed["finish_reason"] == "length"


async def test_optional_null_mark_matches_frozen_fixture(tmp_path: Path) -> None:
    ledger = budget(tmp_path / "budget.sqlite")
    body = response()
    function = body["choices"][0]["message"]["tool_calls"][0]["function"]
    arguments = json.loads(function["arguments"])
    arguments["mark"] = None
    function["arguments"] = json.dumps(arguments)
    model = OneBookingModel(
        "mock-credential-for-tests",
        ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        execute=True,
        confirmed_current_pricing=True,
    )
    result = await run_local(model)
    assert not result.get("error")
    assert result["result"]["authorization_status"] == "REJECTED"
    assert model.diagnostics()["argument_mark_state"] == "NULL"
