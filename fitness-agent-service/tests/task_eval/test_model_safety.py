from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.evaluation.task_eval.model_safety import BudgetLedger, ModelPreflightConfig, preflight


def config(**overrides):
    values = {
        "endpoint": "https://model.example.com/v1",
        "allowed_hosts": ["model.example.com"],
        "model": "synthetic-test-model",
        "environment_id": "isolated-eval-001",
        "isolation_evidence_ref": "ticket:iso-001",
        "authorization_ref": "ticket:auth-001",
        "provider_spend_limit_ref": "ticket:spend-001",
        "credential_env": "FITNESS_EVAL_MODEL_API_KEY",
        "max_requests": 2,
        "max_input_tokens": 200,
        "max_output_tokens": 100,
        "max_cost": "1",
        "input_cost_per_million": "100",
        "output_cost_per_million": "200",
        "currency": "USD",
        "pricing_version": "price-v1",
        "data_class": "synthetic",
    }
    return ModelPreflightConfig.model_validate(values | overrides)


def test_preflight_cannot_assert_external_isolation():
    assert preflight(config(), credential_available=True)["agent_model_sandbox_enabled"] is False
    assert (
        preflight(config(), credential_available=False)["local_configuration"]
        == "MISSING_EVAL_CREDENTIAL"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"endpoint": "http://model.example.com/v1"},
        {"endpoint": "https://model.example.com.evil.test/v1"},
        {"endpoint": "https://user:pass@model.example.com/v1"},
        {"endpoint": "https://model.example.com/v1?secret=x"},
        {"endpoint": "https://127.0.0.1/v1", "allowed_hosts": ["127.0.0.1"]},
        {"allowed_hosts": ["machine.internal"]},
        {"credential_env": "DEEPSEEK_API_KEY"},
        {"data_class": "historical_deidentified"},
        {"input_cost_per_million": "0"},
        {"output_cost_per_million": "0"},
        {"max_cost": "NaN"},
        {"max_cost": "0"},
        {"input_cost_per_million": "1e-10000"},
        {"extra_field": "unknown"},
    ],
)
def test_rejects_unsafe_configuration(overrides):
    with pytest.raises(ValidationError):
        config(**overrides)


def test_budget_reservations_enforce_requests_tokens_and_cost():
    ledger = BudgetLedger(config(max_requests=2, max_cost="0.01"))
    first = ledger.reserve(input_tokens_upper_bound=50, output_tokens_upper_bound=10)
    assert first.cost == Decimal("0.007")
    with pytest.raises(ValueError):
        ledger.reserve(input_tokens_upper_bound=1, output_tokens_upper_bound=1)
    ledger.settle(first, input_tokens=20, output_tokens=5)
    assert ledger.cost_committed == Decimal("0.003")
    second = ledger.reserve(input_tokens_upper_bound=50, output_tokens_upper_bound=10)
    ledger.settle(second, input_tokens=1, output_tokens=1)
    with pytest.raises(ValueError):
        ledger.reserve(input_tokens_upper_bound=1, output_tokens_upper_bound=1)
    with pytest.raises(ValueError):
        ledger.settle(second, input_tokens=1, output_tokens=1)


@pytest.mark.parametrize("input_tokens,output_tokens", [(None, 1), (11, 1), (True, 1), (-1, 1)])
def test_unknown_or_excess_usage_burns_reservation_and_stops(input_tokens, output_tokens):
    ledger = BudgetLedger(config())
    reservation = ledger.reserve(input_tokens_upper_bound=10, output_tokens_upper_bound=5)
    ledger.settle(reservation, input_tokens=input_tokens, output_tokens=output_tokens)
    assert ledger.stopped
    assert ledger.input_committed == 10 and ledger.output_committed == 5
    assert ledger.cost_committed == reservation.cost
    with pytest.raises(ValueError):
        ledger.reserve(input_tokens_upper_bound=1, output_tokens_upper_bound=1)


def test_preflight_cli_does_not_print_credentials_or_unsafe_input(tmp_path, capsys, monkeypatch):
    from app.evaluation.task_eval.cli import main

    config_path = tmp_path / "config.json"
    config_path.write_text(config().model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("FITNESS_EVAL_MODEL_API_KEY", "test-secret-do-not-print")
    assert main(["model-preflight", "--config", str(config_path)]) == 3
    output = capsys.readouterr()
    assert "test-secret-do-not-print" not in output.out + output.err
    assert '"network_contacted": false' in output.out
    config_path.write_text('{"api_key": "unsafe-secret-do-not-print"}', encoding="utf-8")
    assert main(["model-preflight", "--config", str(config_path)]) == 1
    output = capsys.readouterr()
    assert "unsafe-secret-do-not-print" not in output.out + output.err
