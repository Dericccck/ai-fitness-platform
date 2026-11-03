"""Real official-SDK Streamable HTTP app; no model or Agent dependencies."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fitness_tool_contracts import (
    CONTRACT_VERSION,
    TOOL_SPECS,
    build_gateway_command,
    canonical_json_bytes,
)
from fitness_tool_contracts.catalog import error_envelope
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import Settings
from .gateway import GatewayAdapter, ToolFailure
from .metrics import Metrics
from .security import AuthenticationFailure, Security

PROTOCOL_VERSION = "2026-07-28"
_logger = logging.getLogger("fitness.mcp")
_SENSITIVE_HEADERS = frozenset(
    {
        b"authorization",
        b"x-agent-context",
        b"x-confirmation-token",
        b"x-request-id",
        b"x-trace-id",
        b"x-tool-deadline",
        b"x-tool-contract-version",
        b"mcp-protocol-version",
    }
)


def _result(envelope: dict[str, Any], *, is_error: bool) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=canonical_json_bytes(envelope).decode())],
        structuredContent=envelope,
        isError=is_error,
    )


class Boundary:
    """Authenticate each HTTP message, before MCP creates work or parses arguments."""

    def __init__(self, app: Any, security: Security):
        self.app = app
        self.security = security

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] != "/mcp":
            await self.app(scope, receive, send)
            return
        if not self.security.configured:
            await JSONResponse({"error": "adapter_not_configured"}, status_code=503)(
                scope, receive, send
            )
            return
        seen = set()
        for name, _ in scope.get("headers", []):
            name = name.lower()
            if name in _SENSITIVE_HEADERS:
                if name in seen:
                    await JSONResponse({"error": "duplicate_security_header"}, status_code=400)(
                        scope, receive, send
                    )
                    return
                seen.add(name)
        request = Request(scope)
        try:
            principal = self.security.authenticate(request.headers.get("authorization", ""))
        except AuthenticationFailure:
            await JSONResponse(
                {"error": "invalid_service_credential"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="fitness-mcp"'},
            )(scope, receive, send)
            return
        if request.headers.get("mcp-protocol-version") != PROTOCOL_VERSION:
            await JSONResponse(
                {"error": "unsupported_protocol_version", "supported_versions": [PROTOCOL_VERSION]},
                status_code=400,
            )(scope, receive, send)
            return
        scope.setdefault("state", {})["mcp_principal"] = principal
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, *, gateway_client: Any = None) -> Any:
    settings = settings or Settings()
    security = Security(settings)
    gateway = GatewayAdapter(settings, gateway_client)
    running = False
    metrics = Metrics()

    async def list_tools(ctx: Any, params: Any) -> ListToolsResult:
        principal = ctx.request.scope["state"]["mcp_principal"]
        allowed = [spec for spec in TOOL_SPECS.values() if spec.required_scope in principal.scopes]
        cursor = params.cursor if params else None
        try:
            offset = int(cursor) if cursor is not None else 0
            if offset < 0 or offset > len(allowed):
                raise ValueError
        except (ValueError, TypeError):
            from mcp import MCPError
            from mcp.types import INVALID_PARAMS

            raise MCPError(code=INVALID_PARAMS, message="Invalid tool cursor") from None
        items = allowed[offset : offset + settings.page_size]
        tools = [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
                outputSchema=spec.output_schema,
                annotations=ToolAnnotations(
                    readOnlyHint=not spec.write,
                    destructiveHint=spec.write,
                    idempotentHint=not spec.write,
                    openWorldHint=False,
                ),
                _meta={
                    "fitness/contractVersion": CONTRACT_VERSION,
                    "fitness/fingerprint": spec.fingerprint,
                },
            )
            for spec in items
        ]
        next_offset = offset + len(items)
        return ListToolsResult(
            tools=tools,
            nextCursor=str(next_offset) if next_offset < len(allowed) else None,
            ttlMs=60000,
            cacheScope="private",
        )

    async def execute_tool(ctx: Any, params: Any) -> CallToolResult:
        spec = TOOL_SPECS.get(params.name)
        if spec is None:
            from mcp import MCPError
            from mcp.types import INVALID_PARAMS

            raise MCPError(code=INVALID_PARAMS, message="Unknown fitness tool")
        try:
            if ctx.request is None:
                raise AuthenticationFailure("http_context_required")
            context = await security.invocation(ctx.request, write=spec.write)
            arguments = params.arguments if params.arguments is not None else {}
            try:
                command = build_gateway_command(spec.name, arguments)
            except (ValueError, KeyError):
                raise ToolFailure("INVALID_ARGUMENT", "NOT_SENT") from None
            if command.body is not None and len(command.body) > settings.max_request_bytes:
                raise ToolFailure("INPUT_TOO_LARGE", "NOT_SENT")
            envelope = await gateway.execute(spec, command, context)
            result = _result(envelope, is_error=False)
            if len(result.model_dump_json(by_alias=True).encode()) > settings.max_result_bytes:
                raise ToolFailure("RESULT_TOO_LARGE", "UNKNOWN" if spec.write else "REJECTED")
            return result
        except AuthenticationFailure as exc:
            code = (
                "DEADLINE_EXCEEDED"
                if str(exc) == "deadline_exceeded"
                else "FORBIDDEN"
                if str(exc) == "insufficient_scope"
                else "AUTHENTICATION_FAILED"
                if str(exc)
                in {
                    "invalid_service_credential",
                    "invalid_agent_context",
                    "confirmation_required",
                    "invalid_confirmation",
                    "http_context_required",
                }
                else "INVALID_ARGUMENT"
            )
            return _result(error_envelope(code, "NOT_SENT"), is_error=True)
        except ValidationError:
            return _result(error_envelope("INVALID_ARGUMENT", "NOT_SENT"), is_error=True)
        except ToolFailure as exc:
            return _result(exc.envelope, is_error=True)
        except Exception:  # noqa: BLE001 -- security boundary must sanitize all ordinary failures
            # Never serialize an upstream response, signed context, args or traceback.
            # Unknown write outcomes must be reconciled using the original business ID.
            _logger.error("Unexpected adapter failure tool=%s", spec.name)
            return _result(
                error_envelope("ADAPTER_INTERNAL_ERROR", "UNKNOWN" if spec.write else "REJECTED"),
                is_error=True,
            )

    async def call_tool(ctx: Any, params: Any) -> CallToolResult:
        name = params.name if params.name in TOOL_SPECS else "unknown"
        start = time.monotonic()
        status = "cancelled"
        metrics.inflight.labels(name).inc()
        try:
            result = await execute_tool(ctx, params)
            envelope = result.structured_content or {}
            status = (
                "success"
                if not result.is_error
                else "unknown"
                if envelope.get("execution_state") == "UNKNOWN"
                else "rejected"
            )
            return result
        except Exception:
            status = "protocol_error"
            raise
        finally:
            metrics.inflight.labels(name).dec()
            metrics.calls.labels(name, status).inc()
            metrics.duration.labels(name, status).observe(time.monotonic() - start)

    server = Server(
        "fitness-business-tools",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: (
            TOOL_SPECS[name].input_schema if name in TOOL_SPECS else None
        ),
    )
    manager = StreamableHTTPSessionManager(
        server,
        json_response=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=settings.allowed_origins,
        ),
        max_request_body_size=settings.max_request_bytes,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        nonlocal running
        async with manager.run():
            running = True
            try:
                yield
            finally:
                running = False
                await gateway.close()

    async def health(request: Request) -> JSONResponse:
        ready = running and security.ready
        live = request.url.path.endswith("live")
        return JSONResponse(
            {
                "status": "ok" if live or ready else "not_ready",
                "contract_version": CONTRACT_VERSION,
                "protocol_version": PROTOCOL_VERSION,
            },
            status_code=200 if live or ready else 503,
        )

    async def prometheus(request: Request) -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    class MCPRoute:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    asgi = Starlette(
        routes=[
            Route("/metrics", prometheus),
            Route("/health/live", health),
            Route("/health/ready", health),
            Route("/health", health),
            Route("/mcp", MCPRoute(), methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    asgi.state.gateway = gateway
    asgi.state.security = security
    asgi.state.mcp_server = server
    asgi.state.session_manager = manager
    return Boundary(asgi, security)


app = create_app()
