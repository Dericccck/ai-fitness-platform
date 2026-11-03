"""Scoped Gateway credentials configure independent clients without cross-scope fallback."""

from typing import Any

import httpx
import pytest

from app.infrastructure.gateway_client import GatewayClient, GatewayConfigurationError

from .test_gateway_client import build_settings, context

TOKEN_SETTINGS = {
    "execute": "gateway_http_execution_service_token",
    "query": "gateway_query_service_token",
    "reconcile": "gateway_reconciliation_service_token",
}
OPERATION_SCOPE = {
    "operation_id": "operation-1",
    "tool_id": "fitness.booking.create.v1",
    "confirmation_id": "confirmation-1",
    "organization_id": "org-1",
    "actor_id": "coach-1",
    "payload_hash": "a" * 64,
}


async def call_scope(client: GatewayClient, scope: str) -> Any:
    if scope == "execute":
        return await client.get_current_user(context())
    if scope == "query":
        return await client.authorize_student_training_context(context(), "org-1", "student-1")
    return await client.reconcile_tool_operation(**OPERATION_SCOPE)


@pytest.mark.parametrize("scope", ["execute", "query", "reconcile"])
async def test_one_scoped_credential_is_sufficient_for_its_channel_only(scope: str) -> None:
    settings = build_settings(
        gateway_internal_service_token="",
        **{TOKEN_SETTINGS[scope]: f"private-{scope}-credential"},
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-Internal-Service-Token"] == f"private-{scope}-credential"
        if scope == "execute":
            return httpx.Response(200, json={"id": "coach-1", "enabled": True})
        if scope == "query":
            return httpx.Response(
                200,
                json={
                    "organizationId": "org-1",
                    "actorId": "coach-1",
                    "studentId": "student-1",
                    "accessType": "ASSIGNED_COACH",
                },
            )
        assert "X-Agent-Context" not in request.headers
        return httpx.Response(
            200,
            json={
                "operationId": "operation-1",
                "toolId": OPERATION_SCOPE["tool_id"],
                "confirmationId": "confirmation-1",
                "organizationId": "org-1",
                "actorId": "coach-1",
                "payloadHash": "a" * 64,
                "status": "UNKNOWN",
                "resultReference": None,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(settings, transport)
        assert await call_scope(client, scope) is not None
        for other in TOKEN_SETTINGS.keys() - {scope}:
            with pytest.raises(GatewayConfigurationError) as error:
                await call_scope(client, other)
            assert "private" not in str(error.value)
    assert len(requests) == 1
    # API readiness retains its existing query-capability meaning.
    assert settings.gateway_configured == (scope == "query")


@pytest.mark.parametrize("scope", ["execute", "query", "reconcile"])
async def test_scoped_credential_cannot_replace_missing_gateway_address(scope: str) -> None:
    settings = build_settings(gateway_base_url="", **{TOKEN_SETTINGS[scope]: "credential"})

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("No request may be sent without a configured Gateway address")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        with pytest.raises(GatewayConfigurationError, match="地址未配置"):
            await call_scope(GatewayClient(settings, transport), scope)
