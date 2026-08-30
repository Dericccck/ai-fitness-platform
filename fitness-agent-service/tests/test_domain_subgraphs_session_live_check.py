"""验证同会话跨四领域 smoke 的顺序、会话一致性和只读安全边界。"""

from __future__ import annotations

from argparse import Namespace
from typing import Any

import httpx
import pytest

from scripts.domain_subgraphs_session_live_check import (
    DEFAULT_CASES,
    SessionSubgraphsLiveCheckError,
    run_live_check,
    validate_cases,
    validate_response,
)


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        return httpx.Response(200, json=self.responses.pop(0))


def completed(route: str, conversation_id: str = "same-thread") -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "route": route,
        "status": "COMPLETED",
        "tool_steps": 1,
        "answer": "已根据真实只读查询完成回答。",
    }


def test_default_cases_keep_fixed_cross_domain_order() -> None:
    validate_cases(DEFAULT_CASES)
    assert [case.route for case in DEFAULT_CASES] == [
        "FITNESS_COACHING",
        "BOOKING",
        "OPERATIONS",
        "CUSTOMER_SERVICE",
    ]


def test_cases_reject_active_write_request() -> None:
    cases = (
        *DEFAULT_CASES[:-1],
        DEFAULT_CASES[-1].__class__(
            name="customer-service",
            route="CUSTOMER_SERVICE",
            message="请提交客服工单",
        ),
    )

    with pytest.raises(SessionSubgraphsLiveCheckError, match="主动写入意图"):
        validate_cases(cases)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "conversation_id": "same-thread",
            "route": "BOOKING",
            "status": "COMPLETED",
            "tool_steps": 1,
            "answer": "ok",
        },
        {
            "conversation_id": "same-thread",
            "route": "FITNESS_COACHING",
            "status": "CONFIRMATION_REQUIRED",
            "tool_steps": 1,
            "answer": "",
        },
        {
            "conversation_id": "same-thread",
            "route": "FITNESS_COACHING",
            "status": "COMPLETED",
            "tool_steps": 0,
            "answer": "ok",
        },
    ),
)
def test_response_rejects_route_status_or_tool_false_positive(payload: dict[str, Any]) -> None:
    with pytest.raises(SessionSubgraphsLiveCheckError):
        validate_response(DEFAULT_CASES[0], payload)


def test_response_rejects_missing_conversation_id() -> None:
    payload = completed("FITNESS_COACHING")
    payload.pop("conversation_id")
    with pytest.raises(SessionSubgraphsLiveCheckError, match="conversation_id"):
        validate_response(DEFAULT_CASES[0], payload)


async def test_live_check_reuses_one_conversation_without_confirmation(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    conversation_id = "same-thread"
    client = FakeClient([completed(case.route, conversation_id) for case in DEFAULT_CASES])

    await run_live_check(
        Namespace(
            endpoint="http://127.0.0.1:8090",
            conversation_id=conversation_id,
            timeout_seconds=30.0,
        ),
        client=client,  # type: ignore[arg-type]
    )

    assert len(client.requests) == 4
    assert [request["json"]["conversation_id"] for request in client.requests] == [
        conversation_id
    ] * 4
    assert all(
        request["headers"]["X-Agent-Context"] == "signed-context" for request in client.requests
    )
