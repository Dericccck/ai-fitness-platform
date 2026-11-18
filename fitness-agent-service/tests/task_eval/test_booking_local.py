from typing import Any

import httpx
import pytest

from app.evaluation.task_eval import booking_local


@pytest.mark.parametrize(
    "scenario",
    ["approve", "reject", "unauthorized", "duplicate", "conflict", "timeout", "response_lost"],
)
async def test_booking_protocol_scenarios(scenario: str) -> None:
    result = await booking_local.evaluate_case(scenario)
    assert result["status"] == "PASS", result


async def test_evaluator_detects_duplicate_business_write(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def duplicate(self: Any, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result = await original(self, context, payload)
        await original(self, context, payload)
        return result

    monkeypatch.setattr(booking_local.Gateway, "create_booking", duplicate)
    result = await booking_local.evaluate_case("duplicate")
    assert result["status"] == "FAIL"
    assert result["checks"]["one_booking_one_credit"] is False


async def test_evaluator_detects_wrong_booking_time(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def wrong_time(self: Any, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return await original(self, context, {**payload, "startTime": "2030-01-01T04:00:00Z"})

    monkeypatch.setattr(booking_local.Gateway, "create_booking", wrong_time)
    result = await booking_local.evaluate_case("approve")
    assert result["status"] == "FAIL"
    assert result["checks"]["one_booking_one_credit"] is False


async def test_suite_preserves_failures_when_another_case_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(name: str) -> dict[str, Any]:
        if name == "approve":
            return {"case": name, "status": "FAIL"}
        if name == "reject":
            raise RuntimeError("sensitive test detail")
        return {"case": name, "status": "PASS"}

    monkeypatch.setattr(booking_local, "evaluate_case", fake)
    report = await booking_local.evaluate_suite()
    assert report["status"] == "FAIL"
    assert len(report["cases"]) == 7
    assert report["cases"][1]["status"] == "ERROR"
    assert "sensitive test detail" not in str(report)


async def test_chat_api_is_called_before_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient.post
    paths: list[str] = []

    async def tracked(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        paths.append(url)
        return await original(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", tracked)
    result = await booking_local.evaluate_case("approve")
    assert result["status"] == "PASS"
    assert paths[0] == "/api/v1/agent/chat"
    assert all("/confirmations/" in path for path in paths[1:])


async def test_chat_api_error_is_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(self: Any, message: str) -> dict[str, Any]:
        raise httpx.ConnectError("offline fixture")

    monkeypatch.setattr(booking_local.AgentHTTP, "chat", failing)
    result = await booking_local.evaluate_case("approve")
    assert result["status"] == "ERROR"
    assert result["reason"] == "CHAT_API_ERROR_OR_INVALID_RESPONSE"


async def test_wrong_conversation_is_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.AgentHTTP.chat

    async def wrong_conversation(self: Any, message: str) -> dict[str, Any]:
        response = await original(self, message)
        response["conversation_id"] = "different"
        return response

    monkeypatch.setattr(booking_local.AgentHTTP, "chat", wrong_conversation)
    result = await booking_local.evaluate_case("approve")
    assert result["status"] == "FAIL"
    assert result["checks"]["conversation_matches"] is False


async def test_response_lost_after_commit_stays_unknown() -> None:
    result = await booking_local.evaluate_case("response_lost")
    assert result["status"] == "PASS"
    assert result["execution_status"] == "UNKNOWN"
    assert result["business"] == {
        "bookings": 1,
        "credit_delta": -1,
        "audited_commits": 1,
        "gateway_attempts": 1,
        "response_lost_after_commit": True,
    }
