import json

import httpx
import pytest

from app.evaluation.task_eval.agent_http import AgentHTTP


@pytest.mark.asyncio
async def test_chat_session_and_idempotent_decision():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"confirmation_id": "confirm-1"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://test"
    ) as client:
        adapter = AgentHTTP(client, "test-token")
        await adapter.chat("第一轮")
        await adapter.chat("第二轮")
        await adapter.decide("confirm-1", "APPROVE")
        await adapter.decide("confirm-1", "APPROVE")
        with pytest.raises(ValueError):
            await adapter.decide("confirm-1", "REJECT")
    first, second = (json.loads(r.content) for r in requests[:2])
    assert first["conversation_id"] == second["conversation_id"]
    assert requests[0].headers["X-Request-Id"] != requests[1].headers["X-Request-Id"]
    assert json.loads(requests[2].content) == json.loads(requests[3].content)
    assert requests[2].url.path == "/api/v1/agent/confirmations/confirm-1/decisions"


@pytest.mark.asyncio
async def test_terminal_requires_both_statuses():
    states = iter(
        [
            {"authorization_status": "REJECTED", "execution_status": "RUNNING"},
            {"authorization_status": "REJECTED", "execution_status": "NOT_STARTED"},
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(states))),
        base_url="http://test",
    ) as client:
        adapter = AgentHTTP(client, "test-token")
        result = await adapter.await_terminal(
            "id",
            expected_authorization="REJECTED",
            expected_execution={"NOT_STARTED"},
            timeout_seconds=1,
            poll_seconds=0.001,
        )
    assert result["execution_status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_timeout_and_reject_path_injection():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://test",
    ) as client:
        adapter = AgentHTTP(client, "test-token")
        with pytest.raises(ValueError):
            await adapter.confirmation("../secrets")
        with pytest.raises(TimeoutError):
            await adapter.await_terminal(
                "id",
                expected_authorization="REJECTED",
                expected_execution={"NOT_STARTED"},
                timeout_seconds=0.01,
                poll_seconds=0.001,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "confirmation", "decide"])
async def test_identity_is_not_forwarded_to_redirect(operation):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://other.example/collect"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://test", follow_redirects=True
    ) as client:
        adapter = AgentHTTP(client, "sensitive-test-context")
        with pytest.raises(httpx.HTTPStatusError):
            if operation == "chat":
                await adapter.chat("synthetic message")
            elif operation == "confirmation":
                await adapter.confirmation("id")
            else:
                await adapter.decide("id", "APPROVE")
    assert len(requests) == 1
    assert requests[0].url.host == "test"
