from pathlib import Path

import pytest

from scripts.observability_contract_check import (
    EXPECTED_ALERTS,
    ObservabilityContractError,
    extract_alert_names,
    validate_alert_rules,
    validate_prometheus_config,
)

METRICS = """
Counter("http_requests_total", "requests")
Histogram("http_request_duration_seconds", "duration")
Gauge("http_requests_in_progress", "in progress")
Counter("operations_query_events_total", "operations")
Counter("maintenance_runs_total", "maintenance")
Counter("notification_delivery_attempts_total", "notifications")
"""


def _alerts() -> str:
    blocks = []
    for name in sorted(EXPECTED_ALERTS):
        blocks.append(
            f"""      - alert: {name}
        expr: fitness_agent_http_requests_total > 0
        labels:
          severity: warning
          service: fitness-agent
        annotations:
          summary: "告警摘要"
          description: "告警说明"
"""
        )
    return "\n".join(blocks)


def test_extract_alert_names_is_stable() -> None:
    assert extract_alert_names(_alerts()) == EXPECTED_ALERTS


def test_validate_alert_rules_accepts_complete_low_cardinality_rules() -> None:
    names = validate_alert_rules(_alerts(), METRICS)

    assert len(names) == len(EXPECTED_ALERTS)


def test_validate_alert_rules_rejects_undefined_metric() -> None:
    with pytest.raises(ObservabilityContractError, match="未定义"):
        validate_alert_rules(
            _alerts().replace("fitness_agent_http_requests_total", "fitness_agent_unknown_total"),
            METRICS,
        )


def test_validate_alert_rules_rejects_high_cardinality_label() -> None:
    alerts = _alerts().replace(
        "service: fitness-agent",
        "request_id: request-1\n          service: fitness-agent",
        1,
    )
    with pytest.raises(ObservabilityContractError, match="高基数"):
        validate_alert_rules(alerts, METRICS)


def test_validate_prometheus_config_requires_agent_jobs() -> None:
    with pytest.raises(ObservabilityContractError, match="fitness-agent-workers"):
        validate_prometheus_config(
            "rule_files:\n  - fitness-agent-alerts.yml\njob_name: fitness-agent\n"
        )


def test_validate_real_repository_contract() -> None:
    root = Path(__file__).parents[1]
    alerts = (root.parent / "deployment/observability/fitness-agent-alerts.yml").read_text()
    metrics = (root / "app/core/metrics.py").read_text()
    prometheus = (root.parent / "deployment/observability/prometheus.yml").read_text()

    validate_alert_rules(alerts, metrics)
    validate_prometheus_config(prometheus)
