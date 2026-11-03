from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import httpx2
import pytest
import uvicorn
from fitness_tool_contracts import TOOL_SPECS, canonical_json_bytes
from jsonschema import validate
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.main import create_app

SERVICE_TOKEN = "service-token-with-more-than-32-characters-test"
CONTEXT_SECRET = "context-secret-with-more-than-32-characters-test"


def signed_context(subject="user-1"):
    now = int(time.time())
    payload = canonical_json_bytes(
        {
            "sub": subject,
            "orgs": ["org-1"],
            "roles": ["STUDENT"],
            "iat": now,
            "exp": now + 120,
            "nonce": subject,
            "alg": "HS256",
            "kid": "test",
        }
    )
    signature = hmac.new(CONTEXT_SECRET.encode(), payload, hashlib.sha256).digest()
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    return f"{encode(payload)}.{encode(signature)}"


def settings(**overrides):
    values = {
        "environment": "test",
        "service_credentials_json": json.dumps(
            [
                {
                    "principal": "agent",
                    "resource": "http://localhost:8001/mcp",
                    "sha256": hashlib.sha256(SERVICE_TOKEN.encode()).hexdigest(),
                    "scopes": ["fitness:read", "fitness:write"],
                    "expires_at": int(time.time()) + 3600,
                }
            ]
        ),
        "gateway_base_url": "http://gateway",
        "gateway_service_token": "gateway-distinct-token",
        "context_algorithm": "HS256",
        "context_key_id": "test",
        "context_secret": CONTEXT_SECRET,
        "allow_insecure_hs256": True,
        "allowed_hosts": ["127.0.0.1:*"],
        "page_size": 7,
    }
    return Settings(**(values | overrides))


def headers(subject="user-1", **extra):
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Agent-Context": signed_context(subject),
        "X-Request-ID": f"request-{subject}",
        "X-Trace-ID": f"trace-{subject}",
        "X-Tool-Contract-Version": "1",
        "X-Tool-Deadline": str(time.time() + 10),
        **extra,
    }


@contextmanager
def running_service(handler=None, **overrides):
    gateway_client = httpx.AsyncClient(
        base_url="http://gateway",
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json={"id": "user-1", "enabled": True}))
        ),
    )
    app = create_app(settings(**overrides), gateway_client=gateway_client)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", access_log=False, lifespan="on"
    )
    server = uvicorn.Server(config)

    def run_server():
        async def serve():
            try:
                await server.serve(sockets=[sock])
            finally:
                await gateway_client.aclose()

        asyncio.run(serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "service failed startup"
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), "service failed shutdown"


@pytest.mark.asyncio
async def test_real_http_modern_discovery_pagination_and_dto():
    with running_service() as (url, _app):
        async with (
            httpx2.AsyncClient(headers=headers(), timeout=10, trust_env=False) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            assert client.protocol_version == "2026-07-28"
            all_tools = []
            page = await client.list_tools()
            while True:
                all_tools.extend(page.tools)
                if page.next_cursor is None:
                    break
                page = await client.list_tools(cursor=page.next_cursor)
            assert len(all_tools) == 20
            for tool in all_tools:
                assert tool.input_schema == TOOL_SPECS[tool.name].input_schema
                assert tool.output_schema == TOOL_SPECS[tool.name].output_schema
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error is False
            assert result.structured_content["data"]["id"] == "user-1"
            validate(
                result.structured_content, TOOL_SPECS["fitness.user.get_current.v1"].output_schema
            )
        async with httpx.AsyncClient(trust_env=False) as raw:
            assert (await raw.get(url + "/health/ready")).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_users_and_confirmation_headers_never_cross():
    received = []

    def gateway(request):
        token = request.headers["x-agent-context"]
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        subject = payload["sub"]
        received.append(
            (
                subject,
                request.headers["x-request-id"],
                request.headers.get("x-confirmation-token"),
                request.headers["x-internal-service-token"],
            )
        )
        return httpx.Response(200, json={"id": subject, "enabled": True})

    with running_service(gateway) as (url, _app):

        async def invoke(index):
            subject = f"user-{index}"
            async with (
                httpx2.AsyncClient(headers=headers(subject), timeout=10, trust_env=False) as pool,
                Client(
                    streamable_http_client(url + "/mcp", http_client=pool), mode="2026-07-28"
                ) as client,
            ):
                result = await client.call_tool("fitness.user.get_current.v1", {})
                assert result.structured_content["data"]["id"] == subject

        await asyncio.gather(*(invoke(index) for index in range(100)))
    assert len(received) == 100
    assert all(
        request_id == f"request-{subject}"
        and confirmation is None
        and service_token == "gateway-distinct-token"
        for subject, request_id, confirmation, service_token in received
    )


@pytest.mark.asyncio
async def test_credential_fail_closed_expiry_scope_and_invalid_identity():
    with running_service() as (url, _app):
        async with httpx.AsyncClient(trust_env=False) as raw:
            response = await raw.post(
                url + "/mcp", headers={"MCP-Protocol-Version": "2026-07-28"}, json={}
            )
            assert response.status_code == 401
            response = await raw.post(
                url + "/mcp",
                headers={
                    "Authorization": f"Bearer {SERVICE_TOKEN}",
                    "MCP-Protocol-Version": "2025-11-25",
                },
                json={},
            )
            assert response.status_code == 400
        async with (
            httpx2.AsyncClient(
                headers=headers() | {"X-Agent-Context": "forged"}, timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error
            assert result.structured_content["error_code"] == "AUTHENTICATION_FAILED"
    with running_service(service_credentials_json="[]") as (url, _app):
        async with httpx.AsyncClient(trust_env=False) as raw:
            assert (await raw.post(url + "/mcp", headers=headers(), json={})).status_code == 503
            assert (await raw.get(url + "/health/ready")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [".", ".."])
async def test_dot_segment_request_id_never_reaches_gateway(request_id):
    calls = []
    with running_service(lambda request: calls.append(request)) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Request-ID": request_id}), timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error
            assert result.structured_content["error_code"] == "INVALID_ARGUMENT"
            assert result.structured_content["execution_state"] == "NOT_SENT"
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", ["nan", "inf", "-inf", "0", "not-a-number"])
async def test_invalid_deadline_never_reaches_gateway(deadline):
    calls = []
    with running_service(lambda request: calls.append(request)) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Tool-Deadline": deadline}), timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error
            assert result.structured_content["execution_state"] == "NOT_SENT"
    assert not calls


@pytest.mark.asyncio
async def test_write_bytes_once_and_unknown_on_upstream_timeout():
    calls = []

    def gateway(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost after commit")

    payload = {
        "organizationId": "org-1",
        "appointmentId": "booking-1",
        "expectedStartTime": "2026-10-27T01:00:00Z",
    }
    with running_service(gateway) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Confirmation-Token": "trusted-confirmation"}),
                timeout=10,
                trust_env=False,
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.booking.cancel.v1", {"payload": payload})
            assert result.is_error
            assert result.structured_content["execution_state"] == "UNKNOWN"
            assert result.structured_content["data"] is None
    assert len(calls) == 1
    assert calls[0].content == canonical_json_bytes(payload)
    assert calls[0].headers["x-confirmation-token"] == "trusted-confirmation"
    assert calls[0].headers["x-internal-service-token"] != SERVICE_TOKEN


def test_production_forbids_hs256_without_algorithm_fallback():
    with pytest.raises(ValueError):
        settings(environment="production")


@pytest.mark.asyncio
async def test_sse_cancellation_reaches_async_gateway_without_claiming_rollback():
    started = threading.Event()
    cancelled = threading.Event()
    completed = threading.Event()

    async def gateway(request):
        started.set()
        try:
            await asyncio.sleep(10)
            completed.set()
            return httpx.Response(200, json={"id": "user-1", "enabled": True})
        finally:
            cancelled.set()

    with running_service(gateway) as (url, _app):
        async with (
            httpx2.AsyncClient(headers=headers(), timeout=10, trust_env=False) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            task = asyncio.create_task(client.call_tool("fitness.user.get_current.v1", {}))
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(100):
                if cancelled.is_set():
                    break
                await asyncio.sleep(0.01)
            assert cancelled.is_set()
            assert not completed.is_set()


@pytest.mark.asyncio
async def test_independent_server_deadline_cap_and_metrics():
    async def gateway(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"id": "user-1", "enabled": True})

    with running_service(gateway, max_deadline_seconds=0.05) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Tool-Deadline": str(time.time() + 3600)}),
                timeout=10,
                trust_env=False,
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error
            assert result.structured_content["execution_state"] == "REJECTED"
        async with httpx.AsyncClient(trust_env=False) as raw:
            response = await raw.get(url + "/metrics")
            assert response.status_code == 200
            assert (
                'fitness_mcp_tool_calls_total{status="rejected",tool="fitness.user.get_current.v1"} 1.0'
                in response.text
            )
            assert "user-1" not in response.text
            assert SERVICE_TOKEN not in response.text


@pytest.mark.asyncio
async def test_read_only_credential_lists_no_write_tools_and_cannot_write():
    credential = [
        {
            "principal": "read-agent",
            "resource": "http://localhost:8001/mcp",
            "sha256": hashlib.sha256(SERVICE_TOKEN.encode()).hexdigest(),
            "scopes": ["fitness:read"],
            "expires_at": int(time.time()) + 3600,
        }
    ]
    with running_service(service_credentials_json=json.dumps(credential), page_size=20) as (
        url,
        _app,
    ):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Confirmation-Token": "approved"}), timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            tools = await client.list_tools()
            assert len(tools.tools) == 11
            result = await client.call_tool(
                "fitness.training.plan.publish.v1", {"plan_id": "plan-1"}
            )
            assert result.is_error
            assert result.structured_content["error_code"] == "FORBIDDEN"
            assert result.structured_content["execution_state"] == "NOT_SENT"


def test_resource_binding_production_jwks_and_host_validation():
    from app.security import Security

    invalid = [
        {
            "principal": "agent",
            "resource": "http://another-resource/mcp",
            "sha256": hashlib.sha256(SERVICE_TOKEN.encode()).hexdigest(),
            "scopes": ["fitness:read"],
            "expires_at": int(time.time()) + 3600,
        }
    ]
    with pytest.raises(ValueError):
        Security(settings(service_credentials_json=json.dumps(invalid)))
    with pytest.raises(ValueError):
        settings(allowed_hosts=["*"])
    with pytest.raises(ValueError):
        settings(
            environment="production", context_algorithm="RS256", context_jwks_url="http://auth/jwks"
        )


@pytest.mark.asyncio
async def test_rs256_rotation_keys_and_no_hs256_fallback():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    now = int(time.time())
    payload = canonical_json_bytes(
        {
            "sub": "user-1",
            "orgs": ["org-1"],
            "roles": ["STUDENT"],
            "iat": now,
            "exp": now + 120,
            "nonce": "rotation",
            "alg": "RS256",
            "kid": "new-key",
        }
    )
    signature = private.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    token = f"{encode(payload)}.{encode(signature)}"
    with running_service(
        context_algorithm="RS256",
        context_secret="",
        allow_insecure_hs256=False,
        context_public_keys_json=json.dumps({"new-key": public}),
    ) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers() | {"X-Agent-Context": token}, timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert not result.is_error
        async with (
            httpx2.AsyncClient(headers=headers(), timeout=10, trust_env=False) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert result.is_error
            assert result.structured_content["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_expired_service_credential_is_401_and_not_ready():
    credential = [
        {
            "principal": "expired-agent",
            "resource": "http://localhost:8001/mcp",
            "sha256": hashlib.sha256(SERVICE_TOKEN.encode()).hexdigest(),
            "scopes": ["fitness:read"],
            "expires_at": int(time.time()) - 1,
        }
    ]
    with running_service(service_credentials_json=json.dumps(credential)) as (url, _app):
        async with httpx.AsyncClient(trust_env=False) as raw:
            response = await raw.post(
                url + "/mcp", headers=headers() | {"MCP-Protocol-Version": "2026-07-28"}, json={}
            )
            assert response.status_code == 401
            assert (await raw.get(url + "/health/ready")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b'{"success":false,"error":"private upstream text"}'),
        httpx.Response(503, content=b"private upstream text"),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_bad_or_ambiguous_write_response_never_becomes_success(response):
    calls = []

    def gateway(request):
        calls.append(request)
        return response

    with running_service(gateway) as (url, _app):
        async with (
            httpx2.AsyncClient(
                headers=headers(**{"X-Confirmation-Token": "approved"}), timeout=10, trust_env=False
            ) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool("fitness.training.plan.publish.v1", {"plan_id": "p-1"})
            assert result.is_error
            assert result.structured_content["execution_state"] == "UNKNOWN"
            assert "private upstream text" not in result.model_dump_json()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verification_is_inside_server_deadline_budget():
    with running_service(max_deadline_seconds=0.05) as (url, app):
        original_verify = app.security.verifier.verify

        def slow_verify(token):
            time.sleep(0.2)
            return original_verify(token)

        app.security.verifier.verify = slow_verify
        async with (
            httpx2.AsyncClient(headers=headers(), timeout=10, trust_env=False) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            start = time.monotonic()
            result = await client.call_tool("fitness.user.get_current.v1", {})
            assert time.monotonic() - start < 0.18
            assert result.is_error
            assert result.structured_content["error_code"] == "DEADLINE_EXCEEDED"
            assert result.structured_content["execution_state"] == "NOT_SENT"
