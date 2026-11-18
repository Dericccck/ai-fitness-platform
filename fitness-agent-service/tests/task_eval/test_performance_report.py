from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.evaluation.task_eval.performance_report import Input, summarize


def span(value=10, **changes):
    return {
        "duration_ms": value,
        "evidence_ref": "trace-1",
        "start_event": "request",
        "end_event": "response",
        "scope": "task",
        "includes_human_wait": False,
    } | changes


def usage(**changes):
    return {
        "input_tokens": 100,
        "output_tokens": 50,
        "input_price_per_million": "2",
        "output_price_per_million": "8",
        "currency": "CNY",
        "usage_evidence_ref": "usage-1",
        "pricing_evidence_ref": "pricing-1",
    } | changes


def sample(trial_id="1", **changes):
    return {
        "trial_id": trial_id,
        "model_mode": "real",
        "business_mode": "real",
        "judge_mode": "real",
        "cache_state": "cold",
        "concurrency": 1,
        "timed_out": False,
        "outcome_evidence_ref": "result-trace-1",
        "measurements": {"e2e": span(), "model": span(4, scope="model")},
        "model_usage": usage(),
        "judge_usage": usage(input_tokens=50, output_tokens=10),
    } | changes


def report(*records):
    return summarize(Input.model_validate({"records": list(records)}))


def test_real_and_stub_warm_cold_concurrency_are_never_mixed():
    result = report(
        sample("a"),
        sample("b", model_mode="stub"),
        sample("c", business_mode="stub"),
        sample("d", cache_state="warm"),
        sample("e", concurrency=2),
    )
    assert result["record_count"] == 5
    assert len(result["groups"]) == 5
    assert {group["execution_mode"] for group in result["groups"]} == {"real", "mixed"}
    assert all(group["trial_count"] == 1 for group in result["groups"])
    assert all(
        series["small_sample"] for group in result["groups"] for series in group["latencies"]
    )
    assert all(
        group["model_cost"]["total_cost"] is None
        for group in result["groups"]
        if group["model_mode"] == "stub"
    )


def test_nearest_rank_timeout_and_evidence_are_not_confused():
    result = report(
        sample("a", measurements={"e2e": span(10)}),
        sample("b", measurements={"e2e": span(20)}),
        sample("c", timed_out=True, measurements={"e2e": span(999)}),
    )
    group = result["groups"][0]
    metric = group["latencies"][0]
    assert group["timeout_count"] == 1
    assert group["timeout_evidence"] == [{"trial_id": "c", "evidence_ref": "result-trace-1"}]
    assert metric["sample_count"] == 2 and metric["p50_ms"] == 10 and metric["p95_ms"] == 20
    assert metric["timeout_censored_count"] == 1
    assert [item["censored"] for item in metric["evidence"]] == [False, False, True]
    assert group["measurement_coverage"]["model"] == {"observed_trials": 0, "missing_trials": 3}


def test_latency_boundaries_and_scopes_never_get_pooled():
    records = [
        sample("a", measurements={"e2e": span(10)}),
        sample("b", measurements={"e2e": span(20, includes_human_wait=True)}),
        sample("c", measurements={"e2e": span(30, end_event="first-answer")}),
    ]
    values = report(*records)["groups"][0]["latencies"]
    assert len(values) == 3 and all(row["sample_count"] == 1 for row in values)


def test_model_and_judge_estimates_are_separate_and_evidence_linked():
    group = report(sample())["groups"][0]
    assert group["model_cost"]["total_cost"] == "0.0006"
    assert group["judge_cost"]["total_cost"] == "0.00018"
    assert group["model_cost"]["trials"][0]["usage_evidence_ref"] == "usage-1"
    assert group["model_cost"]["trials"][0]["pricing_evidence_ref"] == "pricing-1"


@pytest.mark.parametrize(
    "changed",
    [
        {"input_tokens": None},
        {"output_tokens": None},
        {"input_price_per_million": None},
        {"output_price_per_million": None},
        {"currency": None},
        {"usage_evidence_ref": None},
        {"pricing_evidence_ref": None},
    ],
)
def test_missing_usage_price_or_evidence_never_becomes_zero(changed):
    group = report(sample(model_usage=usage(**changed)))["groups"][0]
    result = group["model_cost"]
    assert result["total_cost"] is None and result["priced_trials"] == 0
    assert result["trials"][0]["estimated_cost"] is None
    assert result["trials"][0]["status"] == "USAGE_PRICE_OR_EVIDENCE_MISSING"


def test_partial_and_multiple_currency_costs_do_not_become_totals():
    partial = report(sample("a"), sample("b", model_usage=usage(input_tokens=None)))["groups"][0][
        "model_cost"
    ]
    assert partial["total_cost"] is None and partial["priced_trials"] == 1
    assert partial["known_subtotal_by_currency"] == {"CNY": "0.0006"}
    mixed = report(sample("a"), sample("b", model_usage=usage(currency="USD")))["groups"][0][
        "model_cost"
    ]
    assert mixed["total_cost"] is None and set(mixed["known_subtotal_by_currency"]) == {
        "CNY",
        "USD",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("concurrency", True),
        ("concurrency", 0),
        ("timed_out", "false"),
        ("model_mode", "real_and_stub"),
        ("cache_state", "hot"),
    ],
)
def test_invalid_group_definitions_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Input.model_validate({"records": [sample(**{field: value})]})


@pytest.mark.parametrize("value", [True, -1, "15", float("inf"), float("nan")])
def test_invalid_duration_not_silently_coerced(value):
    with pytest.raises(ValidationError):
        Input.model_validate({"records": [sample(measurements={"e2e": span(value)})]})


def test_duplicate_trials_and_invalid_price_rejected():
    with pytest.raises(ValidationError):
        Input.model_validate({"records": [sample(), sample()]})
    with pytest.raises(ValidationError):
        Input.model_validate(
            {"records": [sample(model_usage=usage(input_price_per_million="NaN"))]}
        )


def test_missing_evidence_and_different_mode_are_explicit():
    row = deepcopy(
        sample(
            model_mode="unknown",
            model_usage=None,
            judge_mode="not_used",
            judge_usage=None,
            measurements={},
        )
    )
    group = report(row)["groups"][0]
    assert group["execution_mode"] == "unknown"
    assert group["latencies"] == []
    assert group["measurement_coverage"]["e2e"]["missing_trials"] == 1
    assert group["model_cost"]["total_cost"] is None
    assert group["judge_cost"]["total_cost"] is None


def test_all_timeouts_produce_no_completion_percentiles():
    group = report(sample(timed_out=True))["groups"][0]
    e2e = next(item for item in group["latencies"] if item["metric"] == "e2e")
    assert e2e["sample_count"] == 0
    assert e2e["p50_ms"] is None and e2e["p95_ms"] is None
    assert e2e["timeout_censored_count"] == 1


def test_judge_stub_makes_mixed_mode_and_cost_unknown():
    group = report(sample(judge_mode="stub"))["groups"][0]
    assert group["execution_mode"] == "mixed"
    assert group["judge_cost"]["total_cost"] is None
    assert group["model_cost"]["total_cost"] == "0.0006"


def test_precision_for_large_decimal_inputs_is_preserved():
    cost = usage(
        input_tokens=1_000_000_000,
        output_tokens=1_000_000_000,
        input_price_per_million="999999999999.999999999999",
        output_price_per_million="0.000000000001",
    )
    group = report(sample(model_usage=cost))["groups"][0]
    assert group["model_cost"]["total_cost"] == "1000000000000000.000000000000"


def test_cli_rejects_empty_input_without_fabricating_output(tmp_path, capsys, monkeypatch):
    import json
    import sys

    from app.evaluation.task_eval.performance_report import main

    path = tmp_path / "records.json"
    path.write_text('{"records": []}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["performance_report", str(path)])
    assert main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "ERROR", "reason": "INVALID_INPUT"}
