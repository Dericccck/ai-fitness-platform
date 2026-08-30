"""验证四领域真实 smoke 脚本不会写业务数据并严格检查公开结果。"""

from __future__ import annotations

from argparse import Namespace
from typing import Any

import httpx
import pytest

from scripts.domain_subgraphs_live_check import (
    DEFAULT_CASES,
    DomainSubgraphsLiveCheckError,
    SmokeCase,
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


def completed(route: str) -> dict[str, Any]:
    return {
        "route": route,
        "status": "COMPLETED",
        "tool_steps": 1,
        "answer": "已根据真实查询结果完成回答。",
    }


def test_default_cases_cover_four_routes_without_write_intent() -> None:
    validate_cases(DEFAULT_CASES)
    assert {case.route for case in DEFAULT_CASES} == {
        "FITNESS_COACHING",
        "BOOKING",
        "OPERATIONS",
        "CUSTOMER_SERVICE",
    }


def test_cases_reject_active_write_request() -> None:
    cases = (
        *DEFAULT_CASES[:-1],
        SmokeCase("customer-service", "CUSTOMER_SERVICE", "请帮我提交客服工单"),
    )

    with pytest.raises(DomainSubgraphsLiveCheckError, match="包含写入意图"):
        validate_cases(cases)


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"route": "BOOKING", "status": "COMPLETED", "tool_steps": 1, "answer": "ok"}, "路由错误"),
        (
            {
                "route": "FITNESS_COACHING",
                "status": "CONFIRMATION_REQUIRED",
                "tool_steps": 1,
                "answer": "",
            },
            "非完成状态",
        ),
        (
            {
                "route": "FITNESS_COACHING",
                "status": "COMPLETED",
                "tool_steps": 0,
                "answer": "ok",
            },
            "未完成真实工具调用",
        ),
    ),
)
def test_response_rejects_false_positive(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(DomainSubgraphsLiveCheckError, match=message):
        validate_response(DEFAULT_CASES[0], payload)


def test_booking_response_rejects_unfinished_query_explanation() -> None:
    payload = completed("BOOKING")
    payload["answer"] = "当前还需要查询机构信息，才能继续获取课程。"

    with pytest.raises(DomainSubgraphsLiveCheckError, match="未完成查询说明"):
        validate_response(DEFAULT_CASES[1], payload)


async def test_live_check_calls_each_domain_once_without_confirmation(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    responses = [completed(case.route) for case in DEFAULT_CASES]
    client = FakeClient(responses)

    await run_live_check(
        Namespace(endpoint="http://127.0.0.1:8090", timeout_seconds=30.0),
        client=client,  # type: ignore[arg-type]
    )

    assert len(client.requests) == 4
    assert all(
        request["headers"]["X-Agent-Context"] == "signed-context" for request in client.requests
    )
    assert all("confirmation" not in request["json"] for request in client.requests)
