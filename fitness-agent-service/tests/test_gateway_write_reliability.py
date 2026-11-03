"""业务 POST 发送一次，只有读操作可以在 HTTP 客户端内重试。"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.infrastructure.gateway_client import (
    GatewayClient,
    GatewayConfigurationError,
    GatewayExecutionUnknownError,
    GatewayForbiddenError,
)

from .test_gateway_client import build_settings, context

WRITES: list[tuple[str, tuple[Any, ...]]] = [
    ("create_booking", ({"organizationId": "org-1"},)),
    ("reschedule_booking", ({"appointmentId": "appointment-1"},)),
    ("cancel_booking", ({"appointmentId": "appointment-1"},)),
    ("create_training_draft", ({"organizationId": "org-1"},)),
    ("submit_training_review", ("plan-1",)),
    ("review_training_plan", ("plan-1", {"decision": "APPROVE", "comment": None})),
    ("publish_training_plan", ("plan-1",)),
    ("record_training_day_execution", ("plan-1", "day-1", {"status": "COMPLETED", "note": None})),
    ("create_customer_service_ticket", ({"organizationId": "org-1"},)),
]


@pytest.mark.parametrize(("method", "arguments"), WRITES)
@pytest.mark.parametrize("failure", ["network", "503", "invalid-json", "invalid-view"])
async def test_every_java_write_reports_unknown_and_does_not_retry(
    method: str, arguments: tuple[Any, ...], failure: str
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-Request-ID"] == "request-123"
        if failure == "network":
            raise httpx.ReadTimeout("response lost after dispatch", request=request)
        if failure == "503":
            return httpx.Response(503)
        if failure == "invalid-json":
            return httpx.Response(200, content=b"truncated business response")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(build_settings(gateway_max_retries=5), transport)
        with pytest.raises(GatewayExecutionUnknownError):
            await getattr(client, method)(context(), *arguments)

    assert len(requests) == 1


async def test_explicit_gateway_denial_remains_a_definite_failure() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(build_settings(), transport)
        with pytest.raises(GatewayForbiddenError) as error:
            await client.create_booking(context(), {"organizationId": "org-1"})
    assert error.value.status_code == 403
    assert len(requests) == 1


async def test_full_operation_queries_use_separate_user_and_service_scopes() -> None:
    requests: list[httpx.Request] = []
    scope = {
        "tool_id": "fitness.support.ticket.create.v1",
        "confirmation_id": "confirmation-1",
        "organization_id": "org-1",
        "payload_hash": "a" * 64,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.headers["X-Agent-Context"] == "signed-context"
            assert request.url.params["toolId"] == scope["tool_id"]
            assert request.url.params["confirmationId"] == scope["confirmation_id"]
            assert request.url.params["payloadHash"] == scope["payload_hash"]
            assert request.url.path == "/internal/agent-tools/v1/operations/request-123"
        else:
            assert "X-Agent-Context" not in request.headers
            assert request.url.path == "/internal/agent-reconciliation/v1/operations/request-123"
            import json

            assert json.loads(request.content)["actorId"] == "coach-1"
        return httpx.Response(
            200,
            json={
                "operationId": "request-123",
                "toolId": scope["tool_id"],
                "confirmationId": scope["confirmation_id"],
                "organizationId": scope["organization_id"],
                "actorId": "coach-1",
                "payloadHash": scope["payload_hash"],
                "status": "UNKNOWN",
                "resultReference": None,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(build_settings(), transport)
        foreground = await client.query_tool_operation(
            context(), operation_id="request-123", **scope
        )
        background = await client.reconcile_tool_operation(
            operation_id="request-123", actor_id="coach-1", **scope
        )
    assert foreground == background
    assert foreground.status == "UNKNOWN"
    assert len(requests) == 2


BUSINESS_READS: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
    ("get_current_user", (), {}),
    ("get_organization", ("org-1",), {}),
    ("list_courses", ("org-1",), {}),
    ("list_contracts", ("org-1",), {}),
    ("list_appointments", ("org-1",), {}),
    (
        "check_booking_availability",
        ("org-1",),
        {
            "student_id": "student-1",
            "coach_id": "coach-1",
            "course_id": None,
            "start_time": datetime(2026, 10, 27, 1, tzinfo=UTC),
            "end_time": datetime(2026, 10, 27, 2, tzinfo=UTC),
        },
    ),
    ("query_operations_metric", ("org-1", "appointment_count"), {}),
    ("list_customer_service_tickets", ("org-1",), {}),
    ("get_customer_service_ticket", ("org-1", "ticket-1"), {}),
    ("list_training_day_executions", ("plan-1",), {}),
]
INTERNAL_READS: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
    ("get_training_plan", ("plan-1",), {}),
    ("authorize_student_training_context", ("org-1", "student-1"), {}),
    ("query_booking_operation", ("operation-1",), {}),
    (
        "query_tool_operation",
        (),
        {
            "operation_id": "operation-1",
            "tool_id": "fitness.booking.create.v1",
            "confirmation_id": "confirmation-1",
            "organization_id": "org-1",
            "payload_hash": "a" * 64,
        },
    ),
]


@pytest.mark.parametrize(
    ("method", "arguments", "keywords", "expected_token"),
    [(name, args, kwargs, "execution-token") for name, args, kwargs in BUSINESS_READS]
    + [(name, args, {}, "execution-token") for name, args in WRITES]
    + [(name, args, kwargs, "query-token") for name, args, kwargs in INTERNAL_READS],
)
async def test_http_business_fallback_and_internal_queries_use_their_own_service_scope(
    method: str, arguments: tuple[Any, ...], keywords: dict[str, Any], expected_token: str
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-Internal-Service-Token"] == expected_token
        return httpx.Response(403)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(
            build_settings(
                gateway_query_service_token="query-token",
                gateway_http_execution_service_token="execution-token",
                gateway_reconciliation_service_token="reconciliation-token",
            ),
            transport,
        )
        with pytest.raises(GatewayForbiddenError):
            await getattr(client, method)(context(), *arguments, **keywords)
    assert len(requests) == 1


async def test_query_scope_cannot_be_used_for_business_post() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test"
    ) as transport:
        client = GatewayClient(build_settings(), transport)
        with pytest.raises(GatewayConfigurationError):
            await client._request(
                "/internal/agent-tools/v1/appointments",
                context(),
                {},
                method="POST",
                json_body={},
                service_scope="query",
            )
    assert requests == []
