"""不采样的模型尝试用量：正文不入库，未知用量/价格不伪装为零。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from structlog.contextvars import get_contextvars

from app.core.config import Settings
from app.infrastructure.database import Database


@dataclass(frozen=True)
class ModelUsage:
    call_id: str
    request_id: str
    run_id: str | None
    model: str
    provider: str
    kind: str
    attempt: int
    status: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    duration_seconds: float | None = None
    ttft_seconds: float | None = None
    estimated_cost: str | None = None
    currency: str | None = None
    pricing_version: str | None = None


@dataclass
class UsageCollection:
    request_id: str
    run_id: str | None = None
    calls: list[ModelUsage] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        complete = bool(self.calls) and all(
            c.input_tokens is not None and c.output_tokens is not None for c in self.calls
        )
        priced = complete and all(c.estimated_cost is not None for c in self.calls)
        currencies = {c.currency for c in self.calls}
        return {
            "model_calls": len(self.calls),
            "usage_complete": complete,
            "input_tokens": sum(c.input_tokens or 0 for c in self.calls) if complete else None,
            "output_tokens": sum(c.output_tokens or 0 for c in self.calls) if complete else None,
            "known_input_tokens": sum(c.input_tokens or 0 for c in self.calls),
            "known_output_tokens": sum(c.output_tokens or 0 for c in self.calls),
            "estimated_cost": (
                str(sum((Decimal(c.estimated_cost or "0") for c in self.calls), Decimal(0)))
                if priced and len(currencies) == 1
                else None
            ),
            "currency": next(iter(currencies)) if priced and len(currencies) == 1 else None,
        }


_current: ContextVar[UsageCollection | None] = ContextVar("model_usage", default=None)


@contextmanager
def collect_usage(request_id: str, *, run_id: str | None = None) -> Iterator[UsageCollection]:
    parent = _current.get()
    collection = UsageCollection(request_id, run_id or (parent.run_id if parent else None))
    token = _current.set(collection)
    try:
        yield collection
    finally:
        _current.reset(token)


def usage_context() -> tuple[str, str | None]:
    collection = _current.get()
    if collection:
        return collection.request_id, collection.run_id
    return str(get_contextvars().get("request_id") or "background"), None


def record_usage(usage: ModelUsage) -> None:
    collection = _current.get()
    if collection:
        collection.calls.append(usage)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def extract_usage(raw: Any) -> dict[str, int | None]:
    incoming = _count(getattr(raw, "prompt_tokens", None))
    outgoing = _count(getattr(raw, "completion_tokens", None))
    hit = _count(getattr(raw, "prompt_cache_hit_tokens", None))
    if hit is None:
        hit = _count(getattr(getattr(raw, "prompt_tokens_details", None), "cached_tokens", None))
    miss = _count(getattr(raw, "prompt_cache_miss_tokens", None))
    if incoming is not None and hit is not None and hit <= incoming and miss is None:
        miss = incoming - hit
    if incoming is not None and (
        (hit is not None and hit > incoming)
        or (hit is not None and miss is not None and hit + miss != incoming)
    ):
        hit = miss = None
    return {
        "input_tokens": incoming,
        "output_tokens": outgoing,
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
    }


def estimate_cost(settings: Settings, counts: dict[str, int | None]) -> str | None:
    if settings.llm_pricing_model != settings.llm_model or not settings.llm_pricing_version:
        return None
    incoming, outgoing = counts["input_tokens"], counts["output_tokens"]
    input_rate, output_rate = (
        settings.llm_input_price_per_million,
        settings.llm_output_price_per_million,
    )
    if incoming is None or outgoing is None or input_rate is None or output_rate is None:
        return None
    hit = counts["cache_hit_tokens"]
    cached_rate = settings.llm_cached_input_price_per_million
    if cached_rate is not None and hit is None:
        return None  # 分级价格已配置但供应商未返回命中信息，无法精确估算。
    input_cost = Decimal(incoming) * input_rate
    if cached_rate is not None and hit is not None:
        input_cost = Decimal(incoming - hit) * input_rate + Decimal(hit) * cached_rate
    return str((input_cost + Decimal(outgoing) * output_rate) / Decimal(1_000_000))


class ModelUsageRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def write(self, usage: ModelUsage) -> None:
        params = asdict(usage)
        if params["estimated_cost"] is not None:
            params["estimated_cost"] = Decimal(params["estimated_cost"])
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO agent_model_usage
                  (call_id, request_id, run_id, model, provider, kind, attempt, status,
                   prompt_version, input_tokens, output_tokens, cache_hit_tokens,
                   cache_miss_tokens, duration_seconds, ttft_seconds, estimated_cost,
                   currency, pricing_version)
                VALUES (:call_id, :request_id, :run_id, :model, :provider, :kind, :attempt,
                  :status, :prompt_version, :input_tokens, :output_tokens, :cache_hit_tokens,
                  :cache_miss_tokens, :duration_seconds, :ttft_seconds, :estimated_cost,
                  :currency, :pricing_version)
                ON CONFLICT (call_id) DO UPDATE SET status=EXCLUDED.status,
                  input_tokens=EXCLUDED.input_tokens, output_tokens=EXCLUDED.output_tokens,
                  cache_hit_tokens=EXCLUDED.cache_hit_tokens,
                  cache_miss_tokens=EXCLUDED.cache_miss_tokens,
                  duration_seconds=EXCLUDED.duration_seconds, ttft_seconds=EXCLUDED.ttft_seconds,
                  estimated_cost=EXCLUDED.estimated_cost, currency=EXCLUDED.currency,
                  pricing_version=EXCLUDED.pricing_version, updated_at=CURRENT_TIMESTAMP
                WHERE agent_model_usage.status='STARTED'
            """),
                params,
            )

    async def cleanup(self, days: int, *, batch_size: int = 500) -> int:
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""
                DELETE FROM agent_model_usage WHERE call_id IN (
                  SELECT call_id FROM agent_model_usage
                  WHERE created_at < CURRENT_TIMESTAMP - :days * INTERVAL '1 day'
                  ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED
                ) RETURNING call_id
            """),
                {"days": days, "batch": batch_size},
            )
            return len(result.all())


def usage_json(usage: ModelUsage) -> str:
    return json.dumps(asdict(usage), ensure_ascii=False)
