"""MCP dependency and durable reconciliation gauges without user/request labels."""

from typing import Any

from prometheus_client import CollectorRegistry, Gauge


class McpRuntimeMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.enabled = Gauge(
            "fitness_agent_mcp_enabled", "New actions use the MCP rollout", registry=registry
        )
        self.catalog = Gauge(
            "fitness_agent_mcp_catalog_available",
            "Approved remote catalogue is fresh",
            registry=registry,
        )
        self.pending = Gauge(
            "fitness_agent_confirmation_reconciliation_backlog",
            "Java operations running or awaiting a proven result",
            registry=registry,
        )
        self.oldest = Gauge(
            "fitness_agent_confirmation_reconciliation_oldest_seconds",
            "Age of the oldest unresolved Java operation",
            registry=registry,
        )
        self.manual = Gauge(
            "fitness_agent_confirmation_manual_review_backlog",
            "Local unknown actions requiring manual verification",
            registry=registry,
        )

    def dependency(self, *, enabled: bool, client: Any | None) -> None:
        self.enabled.set(int(enabled))
        self.catalog.set(int(client is not None and client.is_available()))

    def reconciliation(self, stats: dict[str, int | float]) -> None:
        self.pending.set(stats["pending"])
        self.oldest.set(stats["oldest_age_seconds"])
        self.manual.set(stats["manual"])
