from datetime import date

import httpx
import pytest

from app.core.config import Settings
from app.infrastructure.gateway_client import (
    GatewayAuthenticationError,
    GatewayClient,
    GatewayForbiddenError,
    GatewayOperationsMetric,
    GatewayProtocolError,
    GatewayRequestContext,
    GatewayUnavailableError,
    GatewayUser,
)


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "gateway_base_url": "http://gateway.test",
        "gateway_internal_service_token": "service-token",
        "gateway_max_retries": 2,
        "gateway_retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


def context() -> GatewayRequestContext:
    return GatewayRequestContext(
        signed_context="signed-context",
        request_id="request-123",
        trace_id="trace-456",
    )


async def test_client_sends_service_and_signed_context_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Service-Token"] == "service-token"
        assert request.headers["X-Agent-Context"] == "signed-context"
        assert request.headers["X-Request-ID"] == "request-123"
        assert request.headers["X-Trace-ID"] == "trace-456"
        assert request.url.path == "/internal/agent-tools/v1/me"
        return httpx.Response(
            200,
            json={
                "id": "user-1",
                "name": "学员",
                "phone": "13800000000",
                "avatar": None,
                "introduction": None,
                "enabled": True,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        user = await client.get_current_user(context())

    assert isinstance(user, GatewayUser)
    assert user.id == "user-1"


async def test_client_retries_transient_gateway_failures() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"code": "UNAVAILABLE"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        courses = await client.list_courses(context(), "org-1")

    assert courses == []
    assert calls == 3


async def test_client_does_not_retry_forbidden_response() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"code": "FORBIDDEN"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        with pytest.raises(GatewayForbiddenError):
            await client.list_courses(context(), "org-2")

    assert calls == 1


async def test_client_maps_authentication_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "UNAUTHORIZED"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        with pytest.raises(GatewayAuthenticationError):
            await client.get_current_user(context())


async def test_client_maps_exhausted_transient_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"code": "UNAVAILABLE"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(gateway_max_retries=1), http_client)
        with pytest.raises(GatewayUnavailableError):
            await client.get_current_user(context())


async def test_client_queries_operations_metric_with_versioned_http_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/internal/agent-tools/v1/operations/metrics"
        assert request.headers["X-Internal-Service-Token"] == "service-token"
        assert request.headers["X-Agent-Context"] == "signed-context"
        assert request.headers["X-Request-ID"] == "request-123"
        assert request.headers["X-Trace-ID"] == "trace-456"
        assert request.url.params["organizationId"] == "org-1"
        assert request.url.params["metric"] == "REVENUE_AMOUNT"
        assert request.url.params["from"] == "2026-08-01"
        assert request.url.params["to"] == "2026-08-15"
        assert request.url.params["limit"] == "20"
        assert request.url.params["bucket"] == "WEEK"
        return httpx.Response(
            200,
            json={
                "metric": "REVENUE_AMOUNT",
                "bucket": "WEEK",
                "organizationId": "org-1",
                "from": "2026-08-01",
                "to": "2026-08-15",
                "rows": [
                    {"dimension": "2026-08-03", "label": "2026-08-03", "value": 12000},
                    {"dimension": "2026-08-10", "label": "2026-08-10", "value": 18000},
                ],
                "generatedAt": "2026-08-15T10:00:00Z",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        result = await client.query_operations_metric(
            context(),
            "org-1",
            "REVENUE_AMOUNT",
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 15),
            limit=20,
            bucket="WEEK",
        )

    assert isinstance(result, GatewayOperationsMetric)
    assert result.organization_id == "org-1"
    assert [row.value for row in result.rows] == [12000, 18000]


async def test_client_rejects_malformed_operations_metric_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "metric": "REVENUE_AMOUNT",
                "bucket": "WEEK",
                "organizationId": "org-1",
                "from": "2026-08-01",
                "to": "2026-08-15",
                "rows": [{"dimension": "2026-08-03", "label": "2026-08-03", "value": 12000}],
                # generatedAt 缺失，说明 Java Gateway 版本化 Tool View 契约不完整。
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as http_client:
        client = GatewayClient(build_settings(), http_client)
        with pytest.raises(GatewayProtocolError):
            await client.query_operations_metric(context(), "org-1", "REVENUE_AMOUNT")
