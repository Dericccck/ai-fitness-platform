from types import SimpleNamespace

from prometheus_client import CollectorRegistry

from app.core.mcp_metrics import McpRuntimeMetrics


def test_dependency_and_reconciliation_gauges_have_no_identity_labels():
    registry = CollectorRegistry()
    metrics = McpRuntimeMetrics(registry)
    metrics.dependency(enabled=True, client=SimpleNamespace(is_available=lambda: False))
    metrics.reconciliation({"pending": 17, "oldest_age_seconds": 601, "manual": 2})
    assert registry.get_sample_value("fitness_agent_mcp_enabled") == 1
    assert registry.get_sample_value("fitness_agent_mcp_catalog_available") == 0
    assert registry.get_sample_value("fitness_agent_confirmation_reconciliation_backlog") == 17
    assert (
        registry.get_sample_value("fitness_agent_confirmation_reconciliation_oldest_seconds") == 601
    )
    assert registry.get_sample_value("fitness_agent_confirmation_manual_review_backlog") == 2
    assert all(not sample.labels for metric in registry.collect() for sample in metric.samples)
