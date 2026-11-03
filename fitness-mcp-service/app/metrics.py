"""Per-process, low-cardinality Prometheus registry; never label users or requests."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.calls = Counter(
            "fitness_mcp_tool_calls_total",
            "Completed fixed-catalogue tool calls",
            ["tool", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "fitness_mcp_tool_duration_seconds",
            "Tool call wall-clock duration",
            ["tool", "status"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 120),
            registry=self.registry,
        )
        self.inflight = Gauge(
            "fitness_mcp_tool_inflight",
            "In-flight fixed-catalogue tool calls",
            ["tool"],
            registry=self.registry,
        )
