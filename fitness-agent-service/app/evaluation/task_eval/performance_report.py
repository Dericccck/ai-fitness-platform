"""Summarize explicitly supplied performance evidence without executing trials.

References are caller declarations, not verified evidence. No production SLA,
model accuracy, billing completeness, or statistical representativeness is
inferred. E2E durations for timeouts are right-censored and excluded from E2E
percentiles; separately observed spans remain reportable.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["real", "stub", "not_used", "unknown"]
Metric = Literal["e2e", "model_ttft", "first_valid_answer", "model", "tool", "retrieval"]
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?\Z")
_SMALL_SAMPLE = 20


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Measurement(StrictModel):
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    evidence_ref: str = Field(min_length=1, max_length=256)
    start_event: str = Field(min_length=1, max_length=100)
    end_event: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=100)
    includes_human_wait: bool = Field(strict=True)

    @field_validator("duration_ms", mode="before")
    @classmethod
    def no_boolean_or_string_duration(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("duration must be a finite number")  # noqa: TRY004 - Pydantic wraps ValueError as validation failure.
        return value

    @field_validator("evidence_ref", "start_event", "end_event", "scope")
    @classmethod
    def no_blank_label(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("label must not be blank or padded")
        return value


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)
    input_price_per_million: str | None = None
    output_price_per_million: str | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    usage_evidence_ref: str | None = Field(default=None, min_length=1, max_length=256)
    pricing_evidence_ref: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("input_price_per_million", "output_price_per_million")
    @classmethod
    def valid_price(cls, value: str | None) -> str | None:
        if value is not None and not _PRICE.fullmatch(value):
            raise ValueError("price must be a nonnegative decimal string")
        return value

    @field_validator("usage_evidence_ref", "pricing_evidence_ref")
    @classmethod
    def valid_reference(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("reference must not be blank or padded")
        return value


class Trial(StrictModel):
    trial_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    model_mode: Mode
    business_mode: Mode
    judge_mode: Mode
    cache_state: Literal["cold", "warm", "unknown"]
    concurrency: int = Field(ge=1, le=10000, strict=True)
    timed_out: bool = Field(strict=True)
    outcome_evidence_ref: str = Field(min_length=1, max_length=256)
    measurements: dict[Metric, Measurement] = Field(default_factory=dict)
    model_usage: Usage | None = None
    judge_usage: Usage | None = None

    @field_validator("outcome_evidence_ref")
    @classmethod
    def valid_outcome_ref(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("reference must not be blank or padded")
        return value


class Input(StrictModel):
    records: list[Trial] = Field(min_length=1, max_length=100_000)

    @field_validator("records")
    @classmethod
    def unique_trials(cls, records: list[Trial]) -> list[Trial]:
        if len({r.trial_id for r in records}) != len(records):
            raise ValueError("duplicate trial ID")
        return records


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    # Nearest-rank: explicit method matters for small, discrete samples.
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1]


def _classify(model: Mode, business: Mode, judge: Mode) -> str:
    modes = {model, business, judge}
    if "unknown" in modes or modes == {"not_used"}:
        return "unknown"
    if "real" in modes and "stub" in modes:
        return "mixed"
    return "real" if "real" in modes else "stub"


def _cost(usage: Usage | None, mode: Mode) -> tuple[str, Decimal | None, str | None]:
    if mode == "not_used":
        return "NOT_APPLICABLE", None, None
    if mode != "real":
        return "NON_REAL_OR_UNKNOWN_MODE", None, None
    if usage is None or any(
        value is None
        for value in (
            usage.input_tokens,
            usage.output_tokens,
            usage.input_price_per_million,
            usage.output_price_per_million,
            usage.currency,
            usage.usage_evidence_ref,
            usage.pricing_evidence_ref,
        )
    ):
        return "USAGE_PRICE_OR_EVIDENCE_MISSING", None, None
    # The None checks above establish all of these fields; assertions are not
    # used as validation, so running Python with -O does not change behavior.
    if (
        usage.input_tokens is None
        or usage.output_tokens is None
        or usage.input_price_per_million is None
        or usage.output_price_per_million is None
        or usage.currency is None
    ):
        return "USAGE_PRICE_OR_EVIDENCE_MISSING", None, None
    with localcontext() as context:
        context.prec = 60
        cost = (
            Decimal(usage.input_tokens) * Decimal(usage.input_price_per_million)
            + Decimal(usage.output_tokens) * Decimal(usage.output_price_per_million)
        ) / Decimal(1_000_000)
    return "ESTIMATED_FROM_DECLARED_USAGE_AND_PRICE", cost, usage.currency


def _summarize_cost(records: list[Trial], *, category: Literal["model", "judge"]) -> dict[str, Any]:
    statuses: dict[str, int] = defaultdict(int)
    subtotal: dict[str, Decimal] = defaultdict(Decimal)
    details = []
    for record in records:
        mode = record.model_mode if category == "model" else record.judge_mode
        usage = record.model_usage if category == "model" else record.judge_usage
        status, cost, currency = _cost(usage, mode)
        statuses[status] += 1
        if cost is not None and currency is not None:
            with localcontext() as context:
                context.prec = 60
                subtotal[currency] += cost
        details.append(
            {
                "trial_id": record.trial_id,
                "status": status,
                "estimated_cost": str(cost) if cost is not None else None,
                "currency": currency,
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "input_price_per_million": usage.input_price_per_million if usage else None,
                "output_price_per_million": usage.output_price_per_million if usage else None,
                "usage_evidence_ref": usage.usage_evidence_ref if usage else None,
                "pricing_evidence_ref": usage.pricing_evidence_ref if usage else None,
            }
        )
    priced = statuses["ESTIMATED_FROM_DECLARED_USAGE_AND_PRICE"]
    applicable = len(records) - statuses["NOT_APPLICABLE"]
    complete = applicable > 0 and priced == applicable and len(subtotal) == 1
    currency = next(iter(subtotal)) if complete else None
    return {
        "applicable_trials": applicable,
        "priced_trials": priced,
        "status_counts": dict(sorted(statuses.items())),
        "total_cost": str(subtotal[currency]) if currency is not None else None,
        "currency": currency,
        "total_reason": "COMPLETE_DECLARED_INPUTS"
        if complete
        else "INCOMPLETE_OR_MULTIPLE_CURRENCIES",
        "known_subtotal_by_currency": {key: str(value) for key, value in sorted(subtotal.items())},
        "trials": details,
    }


def summarize(data: Input) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[Trial]] = defaultdict(list)
    for record in data.records:
        grouped[
            (
                record.model_mode,
                record.business_mode,
                record.judge_mode,
                record.cache_state,
                record.concurrency,
            )
        ].append(record)
    groups = []
    for key, records in sorted(grouped.items()):
        model_mode, business_mode, judge_mode, cache, concurrency = key
        series: dict[tuple[Any, ...], list[float]] = defaultdict(list)
        censored: dict[tuple[Any, ...], int] = defaultdict(int)
        refs: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            for metric, measurement in record.measurements.items():
                series_key = (
                    metric,
                    measurement.scope,
                    measurement.start_event,
                    measurement.end_event,
                    measurement.includes_human_wait,
                )
                refs[series_key].append(
                    {
                        "trial_id": record.trial_id,
                        "evidence_ref": measurement.evidence_ref,
                        "censored": metric == "e2e" and record.timed_out,
                    }
                )
                if metric == "e2e" and record.timed_out:
                    censored[series_key] += 1
                else:
                    series[series_key].append(measurement.duration_ms)
        latency = []
        for series_key in sorted(set(series) | set(censored)):
            metric, scope, start, end, human_wait = series_key
            values = series[series_key]
            latency.append(
                {
                    "metric": metric,
                    "scope": scope,
                    "start_event": start,
                    "end_event": end,
                    "includes_human_wait": human_wait,
                    "sample_count": len(values),
                    "p50_ms": _percentile(values, 0.50),
                    "p95_ms": _percentile(values, 0.95),
                    "timeout_censored_count": censored[series_key],
                    "small_sample": len(values) < _SMALL_SAMPLE,
                    "evidence": refs[series_key],
                }
            )
        groups.append(
            {
                "execution_mode": _classify(model_mode, business_mode, judge_mode),
                "model_mode": model_mode,
                "business_mode": business_mode,
                "judge_mode": judge_mode,
                "cache_state": cache,
                "concurrency": concurrency,
                "trial_count": len(records),
                "timeout_count": sum(record.timed_out for record in records),
                "timeout_evidence": [
                    {"trial_id": record.trial_id, "evidence_ref": record.outcome_evidence_ref}
                    for record in records
                    if record.timed_out
                ],
                "latencies": latency,
                "measurement_coverage": {
                    metric: {
                        "observed_trials": sum(metric in record.measurements for record in records),
                        "missing_trials": sum(
                            metric not in record.measurements for record in records
                        ),
                    }
                    for metric in (
                        "e2e",
                        "model_ttft",
                        "first_valid_answer",
                        "model",
                        "tool",
                        "retrieval",
                    )
                },
                "model_cost": _summarize_cost(records, category="model"),
                "judge_cost": _summarize_cost(records, category="judge"),
            }
        )
    return {
        "schema_version": "1.0",
        "origin": "caller_supplied_records",
        "record_count": len(data.records),
        "percentile_method": "nearest_rank",
        "small_sample_threshold": _SMALL_SAMPLE,
        "limitations": [
            "References, modes, token counts and prices are caller-declared and not independently verified.",
            "Groups use exact component modes, cache state and concurrency; mixed real/stub trials are separate.",
            "Each latency series requires matching scope, start, end and human-wait definitions; missing timings are omitted.",
            "Timeout E2E durations are censored and excluded from completion percentiles; other observed spans are included.",
            "Small samples are exploratory and cannot establish production SLA or representativeness.",
            "Costs are estimates from complete declared token and price inputs; provider charges are not verified. Judge costs are separate.",
            "A cost record must describe tokens subject to the same declared input/output rates; mixed-rate calls need separate source records.",
        ],
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON object containing explicit records")
    args = parser.parse_args()
    try:
        data = json.loads(
            args.input.read_text(encoding="utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
        result = summarize(Input.model_validate(data))
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "reason": "INVALID_INPUT"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
