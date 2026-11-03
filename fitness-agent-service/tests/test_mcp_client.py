"""Real TCP/SDK tests for the Agent's MCP boundary, plus outcome fault cases."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from fitness_tool_contracts.catalog import TOOL_SPECS, error_envelope, success_envelope
from mcp.server import Server
from mcp.types import CallToolResult, ListToolsResult, Tool

from app.core.config import Settings
from app.infrastructure.gateway_client import (
    GatewayExecutionUnknownError,
    GatewayForbiddenError,
    GatewayProtocolError,
    GatewayRequestContext,
)
from app.infrastructure.mcp_client import McpBusinessToolsClient

USER_TOOL = "fitness.user.get_current.v1"
WRITE_TOOL = "fitness.booking.cancel.v1"
WRITE_ARGS = {
    "payload": {
        "organizationId": "org-1",
        "appointmentId": "a-1",
        "expectedStartTime": "2026-10-27T01:00:00Z",
    }
}


@contextmanager
def remote_server(*, result_factory=None, catalog_fault=None):
    received = []
    calls = []

    async def listing(ctx, params):
        cursor = int(params.cursor) if params and params.cursor else 0
        specs = list(TOOL_SPECS.values())
        tools = [
            Tool(name=s.name, inputSchema=s.input_schema, outputSchema=s.output_schema)
            for s in specs[cursor : cursor + 7]
        ]
        if catalog_fault == "schema" and cursor == 0:
            tools[0].input_schema = {
                "type": "object",
                "properties": {"unapproved": {"type": "string"}},
            }
        if catalog_fault == "duplicate" and cursor == 0:
            tools.append(tools[0])
        return ListToolsResult(
            tools=tools, nextCursor=str(cursor + 7) if cursor + 7 < len(specs) else None
        )

    async def execute(ctx, params):
        headers = ctx.request.headers
        received.append(dict(headers))
        calls.append(params.name)
        if result_factory is not None:
            value = result_factory(params)
            if asyncio.iscoroutine(value):
                value = await value
            return value
        value = success_envelope(
            TOOL_SPECS[params.name],
            {
                "id": headers["x-agent-context"],
                "enabled": True,
            },
        )
        return CallToolResult(content=[], structuredContent=value)

    server = Server(
        "agent-boundary-test",
        on_list_tools=listing,
        on_call_tool=execute,
        get_tool_input_schema=lambda name: TOOL_SPECS[name].input_schema,
    )
    app = server.streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    uv = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
    thread = threading.Thread(target=lambda: asyncio.run(uv.serve(sockets=[sock])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert uv.started
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", received, calls
    finally:
        uv.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def settings(url: str, **values):
    return Settings(
        _env_file=None,
        environment="test",
        mcp_enabled=True,
        mcp_url=url,
        mcp_service_token="test-only-service-token-" + "a" * 32,
        **values,
    )


def context(subject="user-1", *, write=False):
    return GatewayRequestContext(
        subject,
        f"operation-{subject}",
        f"trace-{subject}",
        "confirmation-" + subject if write else None,
        transport="mcp",
        contract_version="1",
    )


async def test_real_http_directory_and_100_concurrent_contexts():
    with remote_server() as (url, received, calls):
        client = McpBusinessToolsClient(settings(url, mcp_timeout_seconds=30))
        try:
            await client.refresh()
            assert client.health["available"] is True

            async def invoke(index):
                subject = f"user-{index}"
                result = await client.call_tool(USER_TOOL, {}, context(subject))
                assert result.id == subject

            await asyncio.gather(*(invoke(index) for index in range(100)))
            assert len(calls) == 100
            for headers in received:
                subject = headers["x-agent-context"]
                assert headers["x-request-id"] == f"operation-{subject}"
                assert headers["x-trace-id"] == f"trace-{subject}"
                assert "x-confirmation-token" not in headers
                assert headers["authorization"] == f"Bearer {client.settings.mcp_service_token}"
        finally:
            await client.close()


@pytest.mark.parametrize("fault", ["schema", "duplicate"])
async def test_unapproved_catalog_prevents_execution(fault):
    with remote_server(catalog_fault=fault) as (url, _, calls):
        client = McpBusinessToolsClient(settings(url))
        try:
            await client.refresh()
            assert not client.is_available()
            with pytest.raises(GatewayProtocolError):
                await client.call_tool(USER_TOOL, {}, context())
            assert calls == []
        finally:
            await client.close()


@pytest.mark.parametrize(
    "state,code,error",
    [
        ("UNKNOWN", "UNAVAILABLE", GatewayExecutionUnknownError),
        ("NOT_SENT", "FORBIDDEN", GatewayForbiddenError),
        ("REJECTED", "FORBIDDEN", GatewayForbiddenError),
    ],
)
async def test_write_error_is_never_a_success_or_replayed(state, code, error):
    def response(_):
        return CallToolResult(
            content=[], structuredContent=error_envelope(code, state), isError=True
        )

    with remote_server(result_factory=response) as (url, received, calls):
        client = McpBusinessToolsClient(settings(url))
        try:
            with pytest.raises(error) as exc:
                await client.call_tool(WRITE_TOOL, WRITE_ARGS, context(write=True))
            if error is GatewayForbiddenError:
                assert exc.value.status_code == 403
            assert calls == [WRITE_TOOL]
            assert received[0]["x-confirmation-token"] == "confirmation-user-1"
        finally:
            await client.close()


async def test_write_timeout_keeps_original_operation_and_only_sends_once():
    async def slow(_):
        await asyncio.sleep(2)
        return CallToolResult(content=[], structuredContent={})

    with remote_server(result_factory=slow) as (url, received, calls):
        client = McpBusinessToolsClient(settings(url, mcp_timeout_seconds=0.5))
        try:
            with pytest.raises(GatewayExecutionUnknownError):
                await client.call_tool(WRITE_TOOL, WRITE_ARGS, context(write=True))
            assert calls == [WRITE_TOOL]
            assert received[0]["x-request-id"] == "operation-user-1"
        finally:
            await client.close()


async def test_bad_write_output_is_unknown():
    def response(_):
        return CallToolResult(
            content=[],
            structuredContent={
                "schema_version": "1",
                "data": {},
                "error_code": None,
                "execution_state": "SUCCEEDED",
            },
        )

    with remote_server(result_factory=response) as (url, _, calls):
        client = McpBusinessToolsClient(settings(url))
        try:
            with pytest.raises(GatewayExecutionUnknownError):
                await client.call_tool(WRITE_TOOL, WRITE_ARGS, context(write=True))
            assert calls == [WRITE_TOOL]
        finally:
            await client.close()


async def test_cancelling_one_call_preserves_shared_pool_and_other_calls():
    started = threading.Event()

    async def response(_):
        if not started.is_set():
            started.set()
            await asyncio.sleep(5)
        return CallToolResult(
            content=[], structuredContent=success_envelope(TOOL_SPECS[USER_TOOL],
                                                          {"id": "user-1", "enabled": True}),
        )

    with remote_server(result_factory=response) as (url, _, calls):
        client = McpBusinessToolsClient(settings(url))
        try:
            first = asyncio.create_task(client.call_tool(USER_TOOL, {}, context()))
            for _ in range(200):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            second = asyncio.create_task(client.call_tool(USER_TOOL, {}, context("user-2")))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert (await second).id == "user-1"
            assert (await client.call_tool(USER_TOOL, {}, context())).id == "user-1"
            assert len(calls) == 3
        finally:
            await client.close()


@pytest.mark.parametrize(
    "change",
    [
        {"mcp_url": "https://user:pass@service/mcp"},
        {"mcp_url": "https://service/mcp?token=secret"},
        {"mcp_tool_ids": ["unknown.tool.v1"]},
        {"mcp_service_token": ""},
    ],
)
def test_mcp_config_rejects_untrusted_targets_and_unknown_tools(change):
    values = {
        "mcp_enabled": True,
        "mcp_url": "https://mcp.internal/mcp",
        "mcp_service_token": "secret",
    }
    with pytest.raises(ValueError):
        Settings(_env_file=None, **(values | change))
