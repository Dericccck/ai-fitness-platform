from dataclasses import dataclass
from time import perf_counter

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(slots=True)
class HttpMetrics:
    """封装 Agent HTTP 服务的 Prometheus 指标。

    指标标签必须保持低基数。这里仅允许 method、route 和 status；route 使用 FastAPI
    路由模板（例如 ``/users/{user_id}``），不能使用包含真实用户 ID 的原始 URL，
    否则每个用户都会创建新的时间序列并最终拖垮 Prometheus。
    """

    registry: CollectorRegistry
    requests_total: Counter
    request_duration_seconds: Histogram
    requests_in_progress: Gauge
    maintenance_runs_total: Counter
    maintenance_items_total: Counter
    memory_candidate_events_total: Counter
    session_summary_events_total: Counter
    session_summary_tokens_total: Counter
    session_summary_chars: Histogram

    @classmethod
    def create(
        cls,
        *,
        service_name: str,
        service_version: str,
        environment: str,
        registry: CollectorRegistry | None = None,
    ) -> "HttpMetrics":
        """创建一组可注入的指标，测试可传独立 Registry 避免全局状态污染。"""

        target_registry = registry or CollectorRegistry(auto_describe=True)
        build_info = Info(
            "build",
            "Agent service build and environment information.",
            namespace="fitness_agent",
            registry=target_registry,
        )
        build_info.info(
            {
                "service": service_name,
                "version": service_version,
                "environment": environment,
            }
        )
        return cls(
            registry=target_registry,
            requests_total=Counter(
                "http_requests_total",
                "Total number of completed HTTP requests.",
                labelnames=("method", "route", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            request_duration_seconds=Histogram(
                "http_request_duration_seconds",
                "HTTP request duration in seconds.",
                labelnames=("method", "route"),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            ),
            requests_in_progress=Gauge(
                "http_requests_in_progress",
                "Current number of HTTP requests being processed.",
                labelnames=("method",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            maintenance_runs_total=Counter(
                "maintenance_runs_total",
                "Total completed background maintenance batches by worker and status.",
                labelnames=("worker", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            maintenance_items_total=Counter(
                "maintenance_items_total",
                "Total items handled by background maintenance workers.",
                labelnames=("worker", "outcome"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            memory_candidate_events_total=Counter(
                "memory_candidate_events_total",
                "Total Memory candidate lifecycle and extraction events.",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_events_total=Counter(
                "session_summary_events_total",
                "Total short-term session summary lifecycle events.",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_tokens_total=Counter(
                "session_summary_tokens_total",
                "Total LLM tokens used by short-term session summaries.",
                labelnames=("direction",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_chars=Histogram(
                "session_summary_chars",
                "Character count of short-term session summary input and output.",
                labelnames=("kind",),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(100, 500, 1000, 2000, 3000, 5000, 10000, 20000),
            ),
        )

    def record_memory_candidate_event(self, event: str, count: int = 1) -> None:
        """记录固定枚举事件；调用方不能把用户 ID、候选 ID 等高基数值作为标签。"""

        if count > 0:
            self.memory_candidate_events_total.labels(event=event).inc(count)

    def record_session_summary_event(self, event: str, count: int = 1) -> None:
        """记录固定枚举摘要事件，禁止把 thread 或用户 ID 放入标签。"""

        if count > 0:
            self.session_summary_events_total.labels(event=event).inc(count)


def _route_template(scope: Scope) -> str:
    """在路由匹配完成后提取模板；未知路径统一归入 ``unmatched``。"""

    route = scope.get("route")
    if isinstance(route, BaseRoute):
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str):
            return route_path
    return "unmatched"


class MetricsMiddleware:
    """记录请求量、耗时和并发数，不读取请求体或响应体。"""

    def __init__(
        self,
        app: ASGIApp,
        metrics: HttpMetrics,
        excluded_paths: frozenset[str] = frozenset({"/metrics"}),
    ) -> None:
        self.app = app
        self.metrics = metrics
        self.excluded_paths = excluded_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.excluded_paths:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        status_code = 500
        started_at = perf_counter()
        in_progress = self.metrics.requests_in_progress.labels(method=method)
        in_progress.inc()

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            # FastAPI 完成路由匹配后才会把 route 放入 scope，因此必须在请求结束时读取。
            route = _route_template(scope)
            duration_seconds = perf_counter() - started_at
            self.metrics.requests_total.labels(
                method=method,
                route=route,
                status=str(status_code),
            ).inc()
            self.metrics.request_duration_seconds.labels(
                method=method,
                route=route,
            ).observe(duration_seconds)
            in_progress.dec()
