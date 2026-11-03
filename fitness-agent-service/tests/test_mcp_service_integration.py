"""Agent Registry -> routed MCP client -> real adapter HTTP -> controlled Java stub.

This verifies the complete Python integration and byte/credential contracts. The HTTP
Gateway boundary is a stub; these tests do not claim Java SQL/transaction acceptance.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import importlib.util
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fitness_tool_contracts import TOOL_SPECS, canonical_json_bytes

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import ToolContext
from app.confirmation.models import ConfirmationRecord
from app.confirmation.normalization import (
    ConfirmationNormalizationContext,
    ConfirmationResourceSnapshot,
)
from app.confirmation.request_body import gateway_confirmation_body
from app.confirmation.token import ConfirmationTokenIssuer
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.business_tools_client import (
    BUSINESS_METHOD_TO_TOOL,
    RoutedBusinessToolsClient,
)
from app.infrastructure.gateway_client import (
    GatewayAuthenticationError,
    GatewayClient,
    GatewayRequestContext,
)
from app.infrastructure.mcp_client import McpBusinessToolsClient

_SERVICE_PACKAGE = "fitness_mcp_integration_adapter"
_SERVICE_TOKEN = "integration-service-only-" + "s" * 48
_GATEWAY_TOKEN = "integration-gateway-only-" + "g" * 48
_SUBJECT = "user-1"
_ROLES = ("STUDENT", "COACH", "ORGANIZATION_ADMIN")


def _load_adapter_package():
    """Load the adapter as another package, never replacing Agent's `app` package."""
    if _SERVICE_PACKAGE not in sys.modules:
        package_dir = Path(__file__).resolve().parents[2] / "fitness-mcp-service" / "app"
        spec = importlib.util.spec_from_file_location(
            _SERVICE_PACKAGE,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_SERVICE_PACKAGE] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{_SERVICE_PACKAGE}.main"), importlib.import_module(
        f"{_SERVICE_PACKAGE}.config"
    )


@dataclass(frozen=True)
class SigningKeys:
    identity_private: Any = field(repr=False)
    confirmation_private: Any = field(repr=False)
    identity_public_pem: str
    confirmation_private_pem: str = field(repr=False)


@pytest.fixture(scope="module")
def keys():
    identity = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    confirmation = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return SigningKeys(
        identity,
        confirmation,
        identity.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        confirmation.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _signed_identity(keys: SigningKeys) -> str:
    now = int(time.time())
    payload = canonical_json_bytes(
        {
            "alg": "RS256",
            "kid": "identity-integration",
            "sub": _SUBJECT,
            "orgs": ["org-1"],
            "roles": list(_ROLES),
            "capabilities": ["TRAINING_PLAN_REVIEW"],
            "qualifications": ["CERTIFIED_COACH"],
            "iat": now,
            "exp": now + 180,
            "nonce": "integration-identity",
        }
    )
    signature = keys.identity_private.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    return f"{_encode(payload)}.{_encode(signature)}"


def _verify_token(token: str, public_key: Any) -> dict[str, Any]:
    encoded_payload, encoded_signature = token.split(".")
    payload = base64.urlsafe_b64decode(encoded_payload + "==")
    signature = base64.urlsafe_b64decode(encoded_signature + "==")
    public_key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
    return json.loads(payload)


@contextmanager
def _adapter_server(keys: SigningKeys, response: Any):
    """Use real TCP and real adapter authentication; only downstream Java is stubbed."""
    service, config = _load_adapter_package()
    received: list[httpx.Request] = []

    def java_stub(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json=response)

    downstream = httpx.AsyncClient(
        base_url="http://controlled-java-stub", transport=httpx.MockTransport(java_stub)
    )
    resource = "http://integration-mcp/mcp"
    credential = {
        "principal": "fitness-agent-integration",
        "resource": resource,
        "sha256": hashlib.sha256(_SERVICE_TOKEN.encode()).hexdigest(),
        "scopes": ["fitness:read", "fitness:write"],
        "expires_at": int(time.time()) + 600,
    }
    service_settings = config.Settings(
        environment="test",
        resource_url=resource,
        service_credentials_json=json.dumps([credential]),
        gateway_base_url="http://controlled-java-stub",
        gateway_service_token=_GATEWAY_TOKEN,
        context_algorithm="RS256",
        context_public_keys_json=json.dumps({"identity-integration": keys.identity_public_pem}),
        allowed_hosts=["127.0.0.1:*"],
        allowed_origins=[],
        page_size=7,
    )
    adapter = service.create_app(service_settings, gateway_client=downstream)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(adapter, log_level="critical", access_log=False, lifespan="on")
    )

    def run_server():
        async def serve():
            try:
                await server.serve(sockets=[sock])
            finally:
                await downstream.aclose()

        asyncio.run(serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert server.started
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", received
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def _agent_stack(url: str):
    settings = Settings(
        _env_file=None,
        environment="test",
        mcp_enabled=True,
        mcp_url=url,
        mcp_service_token=_SERVICE_TOKEN,
        mcp_timeout_seconds=15,
    )
    direct_calls: list[httpx.Request] = []

    def forbidden_direct(request):
        direct_calls.append(request)
        raise AssertionError("MCP integration must not fall back to the Agent's direct HTTP path")

    direct_http = httpx.AsyncClient(
        base_url="http://forbidden-direct-gateway", transport=httpx.MockTransport(forbidden_direct)
    )
    direct = GatewayClient(settings, client=direct_http)
    mcp = McpBusinessToolsClient(settings)
    routed = RoutedBusinessToolsClient(settings, direct, mcp)
    registry = build_fitness_tool_registry(
        cast(GatewayClient, routed), availability_checker=routed.is_available
    )
    return direct_http, mcp, routed, registry, direct_calls


def _local_tool_id(remote: str) -> str:
    return (
        "fitness.operations.metric.query.v1"
        if remote == "fitness.operations.metric.aggregate.v1"
        else remote
    )


def _registry_input(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "fitness.training.plan.review.v1":
        return {"plan_id": arguments["plan_id"], **arguments["payload"]}
    if name == "fitness.training.day.record_execution.v1":
        return {
            "plan_id": arguments["plan_id"],
            "day_id": arguments["day_id"],
            **arguments["payload"],
        }
    if "payload" in arguments:
        return dict(arguments["payload"])
    return dict(arguments)


def _confirmed_context(registry, name, raw, signed, identity, keys):
    local_name = _local_tool_id(name)
    operation = "operation-" + name.replace(".", "-")
    context = GatewayRequestContext(
        signed, operation, "trace-integration", transport="mcp", contract_version="1"
    )
    if not TOOL_SPECS[name].write:
        return context, None, None
    definition = registry.get(local_name)
    bound = registry.bind_context_input(local_name, raw, identity)
    validated = definition.input_model.model_validate(bound)
    policy = definition.confirmation_policy
    assert policy is not None
    resource = None
    if policy.resource_required:
        resource_id = policy.resource_id_builder(validated)
        resource = ConfirmationResourceSnapshot(
            "org-1",
            resource_id,
            1,
            {
                "title": "基础力量",
                "status": "APPROVED",
                "student_id": _SUBJECT,
                "coach_id": "coach-1",
                "goal_type": "STRENGTH",
                "days": [],
            },
        )
    normalization = ConfirmationNormalizationContext(
        operation, "thread-integration", _SUBJECT, _ROLES, ("org-1",), context.trace_id
    )
    action = registry.normalize_confirmation(
        local_name, bound, context=normalization, organization_id="org-1", resource=resource
    )
    body = gateway_confirmation_body(
        local_name,
        json.loads(action.canonical_payload),
        contract_version="1",
        resource_id=action.resource_id,
    )
    now = datetime.now(UTC)
    record = ConfirmationRecord(
        id="confirmation-" + name.replace(".", "-"),
        protocol_version=1,
        thread_id=action.thread_id,
        subject_user_id=action.subject_user_id,
        organization_id=action.organization_id,
        tool_id=action.tool_id,
        risk_level=action.risk_level,
        action=action.action,
        resource_type=action.resource_type,
        resource_id=action.resource_id,
        expected_resource_version=action.expected_resource_version,
        request_id=action.request_id,
        payload_hash=action.payload_hash,
        display_summary=action.display_summary,
        payload_ciphertext=b"integration-ciphertext-not-persisted",
        payload_key_version="integration-key",
        authorization_status="APPROVED",
        execution_status="NOT_STARTED",
        version=1,
        created_at=now,
        expires_at=now + timedelta(minutes=3),
        approved_at=now,
        actor_roles=_ROLES,
        actor_organization_ids=("org-1",),
        transport="mcp",
        contract_version="1",
    )
    jti = "jti-" + name.replace(".", "-")
    record = record.issue_credential(jti, now).claim_execution(now)
    token = ConfirmationTokenIssuer(
        "",
        signing_algorithm="RS256",
        signing_key_id="confirmation-integration",
        signing_private_key_pem=keys.confirmation_private_pem,
    ).issue(record, resource=record.resource_id or "org-1:user-1", jti=jti, request_body=body)
    return replace(context, confirmation_token=token), body, record


PLAN = {
    "id": "p-1",
    "organizationId": "org-1",
    "studentId": "user-1",
    "coachId": "coach-1",
    "title": "基础力量",
    "goalType": "STRENGTH",
    "source": "AGENT",
    "status": "DRAFT",
    "version": 1,
    "createdBy": "user-1",
    "days": [],
}

BOOKING = {
    "id": "a-1",
    "organizationId": "org-1",
    "userId": "user-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2026-10-27T01:00:00Z",
    "endTime": "2026-10-27T02:00:00Z",
    "status": 1,
    "contractId": "contract-1",
    "remainingClassHours": 5,
}

TICKET = {
    "id": "t-1",
    "organizationId": "org-1",
    "subjectUserId": "user-1",
    "createdByUserId": "user-1",
    "category": "GENERAL",
    "source": "AGENT",
    "subject": "请求协助",
    "description": "课程问题",
    "status": "OPEN",
}

EXECUTION = {
    "id": "e-1",
    "planId": "p-1",
    "dayId": "d-1",
    "organizationId": "org-1",
    "studentId": "user-1",
    "status": "COMPLETED",
    "executionDate": "2026-10-27",
    "version": 1,
}

CREATE_BOOKING = {
    "organizationId": "org-1",
    "studentId": "user-1",
    "contractId": "contract-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2026-10-27T01:00:00Z",
    "endTime": "2026-10-27T02:00:00Z",
}

DRAFT = {
    "organizationId": "org-1",
    "studentId": "user-1",
    "coachId": "coach-1",
    "title": "基础力量",
    "goalType": "STRENGTH",
    "sessionMinutes": 45,
    "availableEquipment": [],
    "days": [
        {
            "dayNumber": 1,
            "title": "训练日",
            "items": [{"exerciseName": "深蹲", "sortOrder": 1, "sets": 3, "reps": "10"}],
        }
    ],
}

BASE = "/internal/agent-tools/v1"

CASES = [
    ("fitness.user.get_current.v1", {}, {"id": "user-1", "enabled": True}, "/me"),
    (
        "fitness.organization.get.v1",
        {"organization_id": "org-1"},
        {"id": "org-1"},
        "/organizations/org-1",
    ),
    (
        "fitness.course.list.v1",
        {"organization_id": "org-1", "limit": 2},
        [{"id": "course-1"}],
        "/courses",
    ),
    (
        "fitness.contract.list.v1",
        {"organization_id": "org-1", "user_id": "user-1"},
        [{"id": "contract-1", "organizationId": "org-1", "userId": "user-1"}],
        "/contracts",
    ),
    (
        "fitness.appointment.list.v1",
        {"organization_id": "org-1", "from_time": "2026-10-27T09:00:00+08:00"},
        [{"id": "a-1", "organizationId": "org-1", "userId": "user-1"}],
        "/appointments",
    ),
    (
        "fitness.booking.availability.check.v1",
        {
            "organization_id": "org-1",
            "student_id": "user-1",
            "coach_id": "coach-1",
            "course_id": "course-1",
            "start_time": "2026-10-27T01:00:00Z",
            "end_time": "2026-10-27T02:00:00Z",
        },
        {
            "organizationId": "org-1",
            "studentId": "user-1",
            "coachId": "coach-1",
            "courseId": "course-1",
            "startTime": "2026-10-27T01:00:00Z",
            "endTime": "2026-10-27T02:00:00Z",
            "available": True,
            "reasonCodes": [],
            "conflicts": [],
        },
        "/booking/availability",
    ),
    ("fitness.training.plan.get.v1", {"plan_id": "p-1"}, PLAN, "/training/plans/p-1"),
    (
        "fitness.training.day.executions.list.v1",
        {"plan_id": "p-1"},
        [EXECUTION],
        "/training/plans/p-1/executions",
    ),
    (
        "fitness.support.ticket.list.v1",
        {"organization_id": "org-1", "subject_user_id": "user-1", "status": "OPEN"},
        [TICKET],
        "/customer-service/tickets",
    ),
    (
        "fitness.support.ticket.get.v1",
        {"organization_id": "org-1", "ticket_id": "t-1"},
        TICKET,
        "/customer-service/tickets/t-1",
    ),
    (
        "fitness.operations.metric.aggregate.v1",
        {
            "organization_id": "org-1",
            "metric": "APPOINTMENT_COUNT",
            "from_date": "2026-10-01",
            "to_date": "2026-10-27",
            "bucket": "NONE",
        },
        {
            "metric": "APPOINTMENT_COUNT",
            "organizationId": "org-1",
            "from": "2026-10-01",
            "to": "2026-10-27",
            "rows": [{"dimension": "TOTAL", "label": "预约", "value": 3}],
            "generatedAt": "2026-10-27T01:00:00Z",
        },
        "/operations/metrics",
    ),
    ("fitness.booking.create.v1", {"payload": CREATE_BOOKING}, BOOKING, "/appointments"),
    (
        "fitness.booking.reschedule.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "appointmentId": "a-1",
                "coachId": "coach-1",
                "expectedStartTime": "2026-10-27T01:00:00Z",
                "startTime": "2026-10-27T02:00:00Z",
                "endTime": "2026-10-27T03:00:00Z",
            }
        },
        BOOKING,
        "/appointments/a-1/reschedule",
    ),
    (
        "fitness.booking.cancel.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "appointmentId": "a-1",
                "expectedStartTime": "2026-10-27T01:00:00Z",
            }
        },
        BOOKING | {"cancelled": True},
        "/appointments/a-1/cancel",
    ),
    ("fitness.training.plan.create_draft.v1", {"payload": DRAFT}, PLAN, "/training/plans/drafts"),
    (
        "fitness.training.plan.submit_review.v1",
        {"plan_id": "p-1"},
        PLAN | {"status": "PENDING_REVIEW"},
        "/training/plans/p-1/submit-review",
    ),
    (
        "fitness.training.plan.review.v1",
        {"plan_id": "p-1", "payload": {"decision": "APPROVE", "comment": None}},
        PLAN | {"status": "APPROVED"},
        "/training/plans/p-1/review",
    ),
    (
        "fitness.training.plan.publish.v1",
        {"plan_id": "p-1"},
        PLAN | {"status": "PUBLISHED"},
        "/training/plans/p-1/publish",
    ),
    (
        "fitness.training.day.record_execution.v1",
        {"plan_id": "p-1", "day_id": "d-1", "payload": {"status": "COMPLETED", "note": "完成"}},
        EXECUTION,
        "/training/plans/p-1/days/d-1/execution",
    ),
    (
        "fitness.support.ticket.create.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "category": "GENERAL",
                "subject": "请求协助",
                "description": "课程问题",
            }
        },
        TICKET,
        "/customer-service/tickets",
    ),
]


# Colon is a valid opaque-ID character; bind the known day ID as a suffix.
CASES.append(
    (
        "fitness.training.day.record_execution.v1",
        {"plan_id": "p:1", "day_id": "d:1", "payload": {"status": "COMPLETED", "note": "完成"}},
        EXECUTION | {"planId": "p:1", "dayId": "d:1"},
        "/training/plans/p:1/days/d:1/execution",
    )
)


@pytest.mark.parametrize("name,arguments,response,path", CASES, ids=[case[0] for case in CASES])
async def test_registry_routed_client_real_adapter_all_twenty_tools(
    keys, name, arguments, response, path
):
    signed = _signed_identity(keys)
    identity = AgentContextVerifier(
        "",
        signing_algorithm="RS256",
        verification_public_key_ring={"identity-integration": keys.identity_public_pem},
    ).verify(signed)
    with _adapter_server(keys, response) as (url, received):
        direct_http, mcp, routed, registry, direct_calls = _agent_stack(url)
        try:
            await mcp.refresh()
            assert routed.route_for_tool(_local_tool_id(name)) == ("mcp", "1")
            assert mcp.is_available(name)
            raw = _registry_input(name, arguments)
            context, confirmation_body, record = _confirmed_context(
                registry, name, raw, signed, identity, keys
            )
            result = await registry.invoke(
                _local_tool_id(name), raw, ToolContext(context, identity)
            )
            expected = TOOL_SPECS[name].output_json(response)
            if name == "fitness.operations.metric.aggregate.v1":
                assert result["data"] == expected
                assert "report" in result
            else:
                assert result == expected
            assert len(received) == 1
            request = received[0]
            assert request.url.path == BASE + path
            assert request.method == ("POST" if TOOL_SPECS[name].write else "GET")
            assert request.headers["x-agent-context"] == signed
            assert (
                _verify_token(
                    request.headers["x-agent-context"], keys.identity_private.public_key()
                )["sub"]
                == _SUBJECT
            )
            assert request.headers["x-request-id"] == context.request_id
            assert request.headers["x-trace-id"] == context.trace_id
            assert request.headers["x-internal-service-token"] == _GATEWAY_TOKEN
            assert _SERVICE_TOKEN not in request.headers.values()
            assert not direct_calls
            if record is not None:
                assert request.content == confirmation_body
                assert request.headers["x-confirmation-token"] == context.confirmation_token
                claims = _verify_token(
                    request.headers["x-confirmation-token"], keys.confirmation_private.public_key()
                )
                assert claims["request_body_sha256"] == hashlib.sha256(request.content).hexdigest()
                assert claims["sub"] == _SUBJECT
                assert claims["tool_id"] == _local_tool_id(name)
                assert claims["request_id"] == context.request_id
                assert claims["payload_hash"] == record.payload_hash
                assert claims["jti"] == record.credential_jti
                assert (
                    record.execution_status == "RUNNING"
                )  # SQL consumption is outside this stubbed test.
            else:
                assert "x-confirmation-token" not in request.headers
        finally:
            await mcp.close()
            await direct_http.aclose()


@pytest.mark.parametrize(
    "method",
    ["list_courses", "list_contracts", "list_appointments", "list_customer_service_tickets"],
)
async def test_routed_gateway_none_defaults_survive_real_adapter(keys, method):
    with _adapter_server(keys, []) as (url, received):
        direct_http, mcp, routed, _registry, direct_calls = _agent_stack(url)
        context = GatewayRequestContext(
            _signed_identity(keys),
            "operation-none",
            "trace-none",
            transport="mcp",
            contract_version="1",
        )
        try:
            assert await getattr(routed, method)(context, "org-1") == []
            assert len(received) == 1
            assert dict(received[0].url.params) == {"organizationId": "org-1"}
            assert not direct_calls
        finally:
            await mcp.close()
            await direct_http.aclose()


async def test_routed_python_date_arguments_and_metric_local_name_boundary(keys):
    metric = next(case[2] for case in CASES if case[0] == "fitness.operations.metric.aggregate.v1")
    with _adapter_server(keys, metric) as (url, received):
        direct_http, mcp, routed, _registry, direct_calls = _agent_stack(url)
        context = GatewayRequestContext(
            _signed_identity(keys),
            "operation-date",
            "trace-date",
            transport="mcp",
            contract_version="1",
        )
        try:
            result = await routed.query_operations_metric(
                context,
                "org-1",
                "APPOINTMENT_COUNT",
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 27),
            )
            assert result.from_date == date(2026, 10, 1)
            assert len(received) == 1
            assert dict(received[0].url.params) == {
                "organizationId": "org-1",
                "metric": "APPOINTMENT_COUNT",
                "from": "2026-10-01",
                "to": "2026-10-27",
                "bucket": "NONE",
            }
            assert (
                BUSINESS_METHOD_TO_TOOL["query_operations_metric"]
                == "fitness.operations.metric.aggregate.v1"
            )
            assert not direct_calls
        finally:
            await mcp.close()
            await direct_http.aclose()


async def test_real_adapter_rejects_forged_rs256_identity_before_gateway(keys):
    with _adapter_server(keys, {"id": _SUBJECT, "enabled": True}) as (url, received):
        direct_http, mcp, routed, _registry, direct_calls = _agent_stack(url)
        context = GatewayRequestContext(
            "forged-identity.signature",
            "operation-forged",
            "trace-forged",
            transport="mcp",
            contract_version="1",
        )
        try:
            with pytest.raises(GatewayAuthenticationError):
                await routed.get_current_user(context)
            assert received == []
            assert direct_calls == []
        finally:
            await mcp.close()
            await direct_http.aclose()
