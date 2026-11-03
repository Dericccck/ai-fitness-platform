"""Low-cardinality multimodal worker observability; never include user data."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class MultimodalMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.api_enabled = Gauge(
            "fitness_agent_multimodal_api_enabled",
            "Whether the multimodal API is enabled on this instance",
            registry=registry,
        )
        self.runs = Counter(
            "fitness_agent_multimodal_runs_total",
            "Multimodal worker outcomes",
            ["outcome"],
            registry=registry,
        )
        self.duration = Histogram(
            "fitness_agent_multimodal_run_seconds",
            "Multimodal worker execution duration",
            registry=registry,
        )
        self.cleanup = Counter(
            "fitness_agent_multimodal_cleanup_total",
            "Media cleanup attempts",
            ["kind", "outcome"],
            registry=registry,
        )

    def record_run(self, outcome: str, seconds: float) -> None:
        self.runs.labels(outcome=outcome).inc()
        self.duration.observe(seconds)

    def record_cleanup(self, kind: str, outcome: str) -> None:
        self.cleanup.labels(kind=kind, outcome=outcome).inc()
