from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from app.infrastructure.model_usage import ModelUsage


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
    notification_delivery_attempts_total: Counter
    memory_candidate_events_total: Counter
    session_summary_events_total: Counter
    session_summary_tokens_total: Counter
    session_summary_chars: Histogram
    operations_query_events_total: Counter
    routing_decisions_total: Counter
    model_requests_total: Counter
    model_attempt_duration_seconds: Histogram
    model_ttft_seconds: Histogram
    model_tokens_total: Counter
    model_estimated_cost_total: Counter
    usage_ledger_failures_total: Counter
    protection_rejections_total: Counter
    stream_events_total: Counter
    chat_first_answer_seconds: Histogram
    worker_ready: Gauge
    rag_stage_duration_seconds: Histogram
    trulens_export_batches_total: Counter
    trulens_export_spans_total: Counter
    proactive_decisions_total: Counter
    business_decision_events_total: Counter

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
            "Agent 服务构建和环境信息。",
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
                "已完成 HTTP 请求总数。",
                labelnames=("method", "route", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            request_duration_seconds=Histogram(
                "http_request_duration_seconds",
                "HTTP 请求耗时（秒）。",
                labelnames=("method", "route"),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            ),
            requests_in_progress=Gauge(
                "http_requests_in_progress",
                "当前正在处理的 HTTP 请求数。",
                labelnames=("method",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            maintenance_runs_total=Counter(
                "maintenance_runs_total",
                "按 Worker 和状态统计的已完成后台维护批次总数。",
                labelnames=("worker", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            maintenance_items_total=Counter(
                "maintenance_items_total",
                "后台维护 Worker 处理的条目总数。",
                labelnames=("worker", "outcome"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            notification_delivery_attempts_total=Counter(
                "notification_delivery_attempts_total",
                "按渠道和结果统计的通知渠道投递尝试总数。",
                labelnames=("channel", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            memory_candidate_events_total=Counter(
                "memory_candidate_events_total",
                "Memory 候选生命周期和提取事件总数。",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_events_total=Counter(
                "session_summary_events_total",
                "短期会话摘要生命周期事件总数。",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_tokens_total=Counter(
                "session_summary_tokens_total",
                "短期会话摘要使用的 LLM Token 总数。",
                labelnames=("direction",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            session_summary_chars=Histogram(
                "session_summary_chars",
                "短期会话摘要输入和输出的字符数。",
                labelnames=("kind",),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(100, 500, 1000, 2000, 3000, 5000, 10000, 20000),
            ),
            operations_query_events_total=Counter(
                "operations_query_events_total",
                "经营查询结果和资源保护事件。",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            routing_decisions_total=Counter(
                "routing_decisions_total",
                "Supervisor 混合路由决策；标签只使用固定领域、来源和结果枚举。",
                labelnames=("route", "source", "outcome"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            model_requests_total=Counter(
                "model_requests_total",
                "模型供应商请求结果；标签仅使用回合类型和固定状态。",
                labelnames=("kind", "status"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            model_attempt_duration_seconds=Histogram(
                "model_attempt_duration_seconds",
                "一次实际模型尝试耗时。",
                labelnames=("kind", "status"),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
            ),
            model_ttft_seconds=Histogram(
                "model_ttft_seconds",
                "供应商首个内容片段耗时，不含推理内容。",
                labelnames=("kind",),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
            ),
            model_tokens_total=Counter(
                "model_tokens_total",
                "已知 Token 用量；未知不计入而非推定为零。",
                labelnames=("kind", "direction"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            model_estimated_cost_total=Counter(
                "model_estimated_cost_total",
                "按显式版本价格估算的 API 成本。",
                labelnames=("currency",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            usage_ledger_failures_total=Counter(
                "usage_ledger_failures_total",
                "未采样用量账本持久化失败。",
                namespace="fitness_agent",
                registry=target_registry,
            ),
            protection_rejections_total=Counter(
                "protection_rejections_total",
                "入口保护拒绝请求。",
                labelnames=("reason",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            stream_events_total=Counter(
                "stream_events_total",
                "公开流式事件；只使用固定事件类型。",
                labelnames=("event",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            chat_first_answer_seconds=Histogram(
                "chat_first_answer_seconds",
                "任务开始至第一个有效答案片段。",
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
            ),
            worker_ready=Gauge(
                "worker_ready",
                "Worker 完成装配且尚未进入 drain。",
                namespace="fitness_agent",
                registry=target_registry,
            ),
            rag_stage_duration_seconds=Histogram(
                "rag_stage_duration_seconds",
                "RAG 各阶段耗时，包括失败。",
                labelnames=("stage", "status"),
                namespace="fitness_agent",
                registry=target_registry,
                buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
            ),
            trulens_export_batches_total=Counter(
                "trulens_export_batches_total",
                "TruLens OTEL 在线导出批次结果；status 只允许 SUCCEEDED 或 FAILED。",
                labelnames=("status",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            trulens_export_spans_total=Counter(
                "trulens_export_spans_total",
                "TruLens OTEL 在线导出 Span 数量；status 只允许 SUCCEEDED 或 FAILED。",
                labelnames=("status",),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            proactive_decisions_total=Counter(
                "proactive_decisions_total",
                "主动式 Agent 的结构化决策结果。",
                labelnames=("decision", "source"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
            business_decision_events_total=Counter(
                "business_decision_events_total",
                "经营决策平台从指标检测到效果评估的低基数事件。",
                labelnames=("event", "outcome"),
                namespace="fitness_agent",
                registry=target_registry,
            ),
        )

    def observe_model_usage(self, usage: "ModelUsage") -> None:
        kind = usage.kind
        status = usage.status
        duration = getattr(usage, "duration_seconds", None)
        if duration is not None:
            self.model_attempt_duration_seconds.labels(kind=kind, status=status).observe(duration)
        ttft = getattr(usage, "ttft_seconds", None)
        if ttft is not None:
            self.model_ttft_seconds.labels(kind=kind).observe(ttft)
        for attribute, direction in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_hit_tokens", "cache_hit"),
            ("cache_miss_tokens", "cache_miss"),
        ):
            count = getattr(usage, attribute, None)
            if count is not None:
                self.model_tokens_total.labels(kind=kind, direction=direction).inc(count)
        cost = getattr(usage, "estimated_cost", None)
        if cost is not None:
            self.model_estimated_cost_total.labels(currency=str(usage.currency)).inc(float(cost))

    def record_proactive_decision(self, decision: str, source: str) -> None:
        """记录低基数决策枚举和内容来源，不记录候选或用户标识。"""

        self.proactive_decisions_total.labels(decision=decision, source=source).inc()

    def record_business_decision_event(self, event: str, outcome: str, count: int = 1) -> None:
        """记录决策生命周期事件；标签只能使用固定事件和结果枚举。"""

        if count > 0:
            self.business_decision_events_total.labels(event=event, outcome=outcome).inc(count)

    def record_memory_candidate_event(self, event: str, count: int = 1) -> None:
        """记录固定枚举事件；调用方不能把用户 ID、候选 ID 等高基数值作为标签。"""

        if count > 0:
            self.memory_candidate_events_total.labels(event=event).inc(count)

    def record_notification_delivery_attempt(
        self, channel: str, status: str, count: int = 1
    ) -> None:
        """记录通知渠道投递结果；标签只能使用渠道和固定状态，不能放入通知 ID。"""

        if count > 0:
            self.notification_delivery_attempts_total.labels(channel=channel, status=status).inc(
                count
            )

    def record_session_summary_event(self, event: str, count: int = 1) -> None:
        """记录固定枚举摘要事件，禁止把 thread 或用户 ID 放入标签。"""

        if count > 0:
            self.session_summary_events_total.labels(event=event).inc(count)

    def record_operations_query_event(self, event: str, count: int = 1) -> None:
        """记录经营查询结果和资源保护事件，禁止把机构或请求 ID 放入标签。"""

        if count > 0:
            self.operations_query_events_total.labels(event=event).inc(count)

    def record_routing_decision(
        self, route: str, source: str, outcome: str, count: int = 1
    ) -> None:
        """记录低基数路由结果；禁止把意图原文、会话或请求 ID 放进标签。"""

        allowed_routes = {
            "FITNESS_COACHING",
            "BOOKING",
            "OPERATIONS",
            "CUSTOMER_SERVICE",
            "MULTI_INTENT",
            "CLARIFICATION",
            "OUT_OF_SCOPE",
        }
        if route not in allowed_routes:
            route = "CLARIFICATION"
        if count > 0:
            self.routing_decisions_total.labels(route=route, source=source, outcome=outcome).inc(
                count
            )

    def record_model_request(self, kind: str, status: str, count: int = 1) -> None:
        """记录模型请求结果；禁止把模型名、请求 ID 或供应商错误正文作为标签。"""

        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("模型请求状态必须为 SUCCEEDED 或 FAILED")
        if count > 0:
            self.model_requests_total.labels(kind=kind, status=status).inc(count)

    def record_trulens_export(self, status: str, span_count: int) -> None:
        """记录 TruLens 导出结果；不记录 Trace、记录或用户标识。"""

        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("TruLens 导出状态必须为 SUCCEEDED 或 FAILED")
        if span_count < 0:
            raise ValueError("TruLens 导出 Span 数量不能为负数")
        self.trulens_export_batches_total.labels(status=status).inc()
        self.trulens_export_spans_total.labels(status=status).inc(span_count)


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
