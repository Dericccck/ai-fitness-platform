import re
from time import perf_counter
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

_CONTEXT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
logger = structlog.get_logger(__name__)


def normalize_context_id(raw_value: str | None) -> str | None:
    """校验来自上游的请求标识，避免任意文本进入响应头和日志。

    网关传入的 request_id/trace_id 会参与跨服务检索，但 HTTP Header 本身不可信。
    这里只接受长度受限的字母、数字和少量分隔符；非法值会被丢弃，由服务生成 UUID。
    """

    if raw_value and _CONTEXT_ID_PATTERN.fullmatch(raw_value):
        return raw_value
    return None


class RequestContextMiddleware:
    """为每个 HTTP 请求建立隔离的日志上下文和响应追踪头。

    使用纯 ASGI 中间件而不是 BaseHTTPMiddleware，可以保持流式响应和 contextvars 的
    正常传播。后续 SSE 对话接口、模型回调和 Tool Calling 都可以复用这里绑定的追踪
    字段。中间件不会记录查询参数、请求体或响应体，避免健身档案和用户输入进入普通日志。
    """

    def __init__(self, app: ASGIApp, service_name: str = "fitness-agent-service") -> None:
        self.app = app
        self.service_name = service_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = normalize_context_id(headers.get("x-request-id")) or str(uuid4())
        trace_id = normalize_context_id(headers.get("x-trace-id")) or request_id
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        started_at = perf_counter()
        status_code = 500

        # contextvars 以协程为边界隔离上下文，即使多个请求并发执行也不会串号。
        clear_contextvars()
        bind_contextvars(
            service=self.service_name,
            request_id=request_id,
            trace_id=trace_id,
            http_method=method,
            http_path=path,
        )
        logger.info("http_request_started")

        async def send_with_context(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["X-Trace-ID"] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "http_request_failed",
                http_status=500,
                duration_ms=duration_ms,
            )
            raise
        else:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            logger.info(
                "http_request_completed",
                http_status=status_code,
                duration_ms=duration_ms,
            )
        finally:
            # 清理上下文，防止生命周期较长的任务错误复用已经结束的请求标识。
            clear_contextvars()
