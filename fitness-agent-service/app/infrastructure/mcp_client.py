"""Pinned MCP adapter with request isolation, a fixed catalogue and explicit write outcomes."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from uuid import uuid4

import anyio
import httpx2
import structlog
from fitness_tool_contracts.canonical import canonical_json_bytes
from fitness_tool_contracts.catalog import CONTRACT_VERSION, TOOL_SPECS
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.gateway_client import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayClientError,
    GatewayConfigurationError,
    GatewayConflictError,
    GatewayExecutionUnknownError,
    GatewayForbiddenError,
    GatewayNotFoundError,
    GatewayProtocolError,
    GatewayRequestContext,
    GatewayUnavailableError,
)

logger = structlog.get_logger(__name__)
PROTOCOL_VERSION = "2026-07-28"


class _BorrowedTransport(httpx2.AsyncBaseTransport):
    """A short-lived HTTP client can borrow the process pool without closing it."""

    def __init__(self, pool: httpx2.AsyncBaseTransport) -> None:
        self.pool = pool

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await self.pool.handle_async_request(request)

    async def aclose(self) -> None:
        pass


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _single_boundary_error(group: ExceptionGroup) -> GatewayClientError | None:
    """AnyIO wraps a locally raised boundary error when closing SDK task groups."""
    leaves: list[BaseException] = []

    def collect(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                collect(child)
        else:
            leaves.append(error)

    collect(group)
    return leaves[0] if len(leaves) == 1 and isinstance(leaves[0], GatewayClientError) else None


class McpBusinessToolsClient:
    """Each call owns immutable headers and its SDK Client; no user response cache."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client_factory: Callable[..., httpx2.AsyncClient] = httpx2.AsyncClient,
    ) -> None:
        self.settings = settings
        self._http_client_factory = http_client_factory
        self._verified: frozenset[str] = frozenset()
        self._verified_at = 0.0
        self._last_error: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._slots = asyncio.Semaphore(settings.mcp_max_concurrency)
        self._pool = httpx2.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            limits=httpx2.Limits(
                max_connections=settings.mcp_max_concurrency,
                max_keepalive_connections=settings.mcp_max_concurrency,
            ),
        )

    @property
    def health(self) -> dict[str, Any]:
        return {
            "configured": self.settings.mcp_configured,
            "catalog_verified": bool(self._verified),
            "available": self.is_available(),
            "error": self._last_error,
            "contract_version": CONTRACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
        }

    def is_available(self, tool_id: str | None = None) -> bool:
        fresh = time.monotonic() - self._verified_at <= self.settings.mcp_health_ttl_seconds
        return fresh and bool(self._verified) and (tool_id is None or tool_id in self._verified)

    async def start(self) -> None:
        await self.refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="mcp-catalog-refresh")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.mcp_catalog_refresh_seconds)
            await self.refresh()

    async def close(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        await self._pool.aclose()

    @asynccontextmanager
    async def _client(self, headers: dict[str, str]) -> AsyncIterator[Client]:
        async with self._http_client_factory(
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx2.Timeout(self.settings.mcp_timeout_seconds),
            transport=_BorrowedTransport(self._pool),
        ) as http:
            transport = streamable_http_client(
                self.settings.mcp_url,
                http_client=http,
                max_sse_event_size=self.settings.mcp_max_result_bytes,
            )
            try:
                async with Client(
                    transport,
                    cache=None,
                    mode=PROTOCOL_VERSION,
                    read_timeout_seconds=self.settings.mcp_timeout_seconds,
                ) as client:
                    if client.protocol_version != PROTOCOL_VERSION:
                        raise GatewayProtocolError("MCP 协议版本不兼容")
                    yield client
            except ExceptionGroup as group:
                boundary_error = _single_boundary_error(group)
                if boundary_error is not None:
                    raise boundary_error from group
                raise

    def _headers(self) -> dict[str, str]:
        if not self.settings.mcp_configured:
            raise GatewayConfigurationError("MCP 服务未配置")
        return {
            "Authorization": f"Bearer {self.settings.mcp_service_token}",
            "X-Tool-Contract-Version": CONTRACT_VERSION,
        }

    async def _verify_catalog(
        self,
        client: Client,
        *,
        invoking: str | None = None,
    ) -> frozenset[str]:
        found: dict[str, Any] = {}
        cursor: str | None = None
        visited: set[str] = set()
        for _ in range(100):
            page = await client.list_tools(cursor=cursor, cache_mode="bypass")
            for tool in page.tools:
                if tool.name in found:
                    raise GatewayProtocolError("MCP 工具目录存在重复名称")
                found[tool.name] = tool
            cursor = page.next_cursor
            if not cursor:
                break
            if cursor in visited:
                raise GatewayProtocolError("MCP 工具目录分页无效")
            visited.add(cursor)
        else:
            raise GatewayProtocolError("MCP 工具目录超过分页上限")
        required = set(self.settings.mcp_tool_ids) or set(TOOL_SPECS)
        if invoking is not None:
            required.add(invoking)
        if not required <= found.keys() or found.keys() - TOOL_SPECS.keys():
            raise GatewayProtocolError("MCP 工具目录与允许目录不一致")
        verified: set[str] = set()
        for name in required:
            tool, spec = found[name], TOOL_SPECS[name]
            actual = {
                "name": name,
                "version": CONTRACT_VERSION,
                "input": tool.input_schema,
                "output": tool.output_schema,
            }
            digest = hashlib.sha256(canonical_json_bytes(actual)).hexdigest()
            if digest != spec.fingerprint:
                raise GatewayProtocolError("MCP 工具 Schema 与批准契约不一致")
            verified.add(name)
        return frozenset(verified)

    async def refresh(self) -> None:
        try:
            with anyio.fail_after(self.settings.mcp_timeout_seconds):
                async with self._slots, self._client(self._headers()) as client:
                    self._verified = await self._verify_catalog(client)
            self._verified_at = time.monotonic()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001 - isolate optional remote dependency failures
            self._verified = frozenset()
            self._last_error = type(exc).__name__
            logger.warning("mcp_catalog_unavailable", error_type=self._last_error)

    async def call_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        context: GatewayRequestContext,
    ) -> Any:
        if tool_id not in TOOL_SPECS:
            raise GatewayBadRequestError("未批准的 MCP 工具")
        spec = TOOL_SPECS[tool_id]
        if context.contract_version != CONTRACT_VERSION:
            raise GatewayConfigurationError("已确认动作的 MCP 契约版本不可用")
        if not context.signed_context:
            raise GatewayAuthenticationError("缺少可信用户上下文")
        if spec.write and (not context.confirmation_token or not context.request_id):
            raise GatewayAuthenticationError("MCP 写操作需要确认凭证与稳定业务 ID")
        if context.request_id in {".", ".."}:
            raise GatewayBadRequestError("业务 ID 不能是独立路径段", status_code=400)
        wire_arguments = _json_value(arguments)
        try:
            spec.input_model.model_validate(wire_arguments)
        except ValidationError as exc:
            raise GatewayBadRequestError("MCP 工具参数不符合契约") from exc
        headers = self._headers()
        headers.update(
            {
                "X-Agent-Context": context.signed_context,
                "X-Request-ID": context.request_id or str(uuid4()),
                "X-Tool-Deadline": str(time.time() + self.settings.mcp_timeout_seconds),
            }
        )
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        if context.confirmation_token:
            headers["X-Confirmation-Token"] = context.confirmation_token
        attempted = False
        try:
            with anyio.fail_after(self.settings.mcp_timeout_seconds):
                async with self._slots, self._client(headers) as client:
                    verified = await self._verify_catalog(client, invoking=tool_id)
                    self._verified, self._verified_at = verified, time.monotonic()
                    self._last_error = None
                    attempted = True
                    result = await client.call_tool(tool_id, wire_arguments)
                    value = result.structured_content
                    if len(canonical_json_bytes(value)) > self.settings.mcp_max_result_bytes:
                        raise GatewayProtocolError("MCP 工具结果超过大小上限")
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"schema_version", "data", "error_code", "execution_state"}
                        or value["schema_version"] != CONTRACT_VERSION
                    ):
                        raise GatewayProtocolError("MCP 工具结果格式不兼容")
                    if result.is_error or value["execution_state"] != "SUCCEEDED":
                        self._raise_tool_error(value, write=spec.write)
                    if value["error_code"] is not None:
                        raise GatewayProtocolError("MCP 成功结果包含错误码")
                    return spec.validate_output(value["data"])
        except asyncio.CancelledError:
            raise
        except GatewayClientError as exc:
            if not attempted and isinstance(exc, GatewayProtocolError):
                self._verified = frozenset()
                self._last_error = type(exc).__name__
            if spec.write and attempted and isinstance(exc, GatewayProtocolError):
                raise GatewayExecutionUnknownError("写入结果无法验证，正在核实") from exc
            raise
        except Exception as exc:
            self._last_error = type(exc).__name__
            self._verified = frozenset()
            if spec.write and attempted:
                raise GatewayExecutionUnknownError("MCP 写入结果未知，正在核实") from exc
            raise GatewayUnavailableError("MCP 服务暂时不可用") from exc

    @staticmethod
    def _raise_tool_error(value: dict[str, Any], *, write: bool) -> None:
        state, code = value.get("execution_state"), value.get("error_code")
        if not isinstance(code, str) or state not in {"REJECTED", "NOT_SENT", "UNKNOWN"}:
            raise GatewayProtocolError("MCP 错误结果不符合契约")
        if write and state == "UNKNOWN":
            raise GatewayExecutionUnknownError("业务写入结果未知", code=code)
        errors: dict[str, type[GatewayClientError]] = {
            "AUTHENTICATION_FAILED": GatewayAuthenticationError,
            "FORBIDDEN": GatewayForbiddenError,
            "NOT_FOUND": GatewayNotFoundError,
            "CONFLICT": GatewayConflictError,
            "INVALID_ARGUMENT": GatewayBadRequestError,
            "BAD_REQUEST": GatewayBadRequestError,
        }
        error = errors.get(code)
        if state in {"REJECTED", "NOT_SENT"} and error is not None:
            status = {
                "AUTHENTICATION_FAILED": 401,
                "FORBIDDEN": 403,
                "NOT_FOUND": 404,
                "CONFLICT": 409,
                "INVALID_ARGUMENT": 422,
                "BAD_REQUEST": 400,
            }[code]
            raise error("业务工具明确拒绝了操作", code=code, status_code=status)
        if not write and code in {"PROTOCOL_ERROR", "RESULT_TOO_LARGE"}:
            raise GatewayProtocolError("业务查询结果不符合契约", code=code)
        if write:
            # Unknown error vocabularies never authorize a new write attempt.
            raise GatewayExecutionUnknownError("业务写入需要核实", code=code)
        raise GatewayUnavailableError("业务查询失败", code=code)
