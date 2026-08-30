from scripts.observability_e2e_check import build_e2e_rules, build_prometheus_config


def test_e2e_prometheus_config_connects_alertmanager_and_synthetic_target() -> None:
    config = build_prometheus_config(19093, 18081)

    assert "host.docker.internal:19093" in config
    assert "host.docker.internal:18081" in config
    assert "fitness-agent-e2e" in config


def test_e2e_rule_uses_short_window_without_changing_production_rules() -> None:
    rules = build_e2e_rules()

    assert "FitnessAgentDownE2E" in rules
    assert "for: 2s" in rules
    assert "severity: critical" in rules
