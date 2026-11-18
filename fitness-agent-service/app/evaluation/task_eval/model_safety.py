"""Fail-closed preparation for a future real-model evaluation adapter.

These checks and the in-process reservation ledger cannot prove network isolation
or enforce provider billing. A provider-side hard spend limit and independently
verified environment are still required before enabling a real model adapter.
No model, database, notification service, or other network endpoint is contacted.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext
from threading import Lock
from typing import Protocol, TypeGuard
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$")


class ModelPreflightConfig(BaseModel):
    """Non-secret, explicit evaluation configuration; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    endpoint: str
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    model: str
    environment_id: str
    isolation_evidence_ref: str
    authorization_ref: str
    provider_spend_limit_ref: str
    credential_env: str
    max_requests: int = Field(gt=0, le=1000)
    max_input_tokens: int = Field(gt=0, le=1_000_000_000)
    max_output_tokens: int = Field(gt=0, le=1_000_000_000)
    max_cost: str
    input_cost_per_million: str
    output_cost_per_million: str
    currency: str
    pricing_version: str
    data_class: str

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def freeze_hosts(cls, hosts: object) -> object:
        return tuple(hosts) if isinstance(hosts, list) else hosts

    @field_validator("allowed_hosts")
    @classmethod
    def valid_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        if len(hosts) != len(set(hosts)) or any(not _HOST.fullmatch(host) for host in hosts):
            raise ValueError("allowed_hosts must contain unique lowercase DNS names")
        if any(host.endswith((".local", ".internal", ".localhost")) for host in hosts):
            raise ValueError("local or internal targets are prohibited")
        return hosts

    @model_validator(mode="after")
    def validate_safety(self) -> ModelPreflightConfig:
        try:
            endpoint = urlsplit(self.endpoint)
            host = endpoint.hostname
            port = endpoint.port
        except ValueError as exc:
            raise ValueError("invalid endpoint") from exc
        if (
            endpoint.scheme != "https"
            or not host
            or host not in self.allowed_hosts
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
            or port not in (None, 443)
            or endpoint.path not in ("", "/", "/v1", "/v1/")
        ):
            raise ValueError("endpoint must be an explicitly allowed HTTPS provider endpoint")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP-address endpoint is not permitted")
        if not _MODEL.fullmatch(self.model):
            raise ValueError("invalid model identifier")
        if self.credential_env != "FITNESS_EVAL_MODEL_API_KEY":
            raise ValueError("a dedicated evaluation credential environment variable is required")
        if self.data_class != "synthetic":
            raise ValueError("only synthetic evaluation data is permitted")
        if not all(
            _REF.fullmatch(x)
            for x in (
                self.environment_id,
                self.isolation_evidence_ref,
                self.authorization_ref,
                self.provider_spend_limit_ref,
                self.pricing_version,
            )
        ):
            raise ValueError(
                "environment, authorization, provider limit and pricing references are required"
            )
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("currency must be a three-letter code")
        budget = _decimal(self.max_cost, positive=True)
        _decimal(self.input_cost_per_million, positive=True)
        _decimal(self.output_cost_per_million, positive=True)
        if budget <= 0:
            raise ValueError("max_cost must be positive")
        return self


def _decimal(value: str, *, positive: bool) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or result < 0
        or result > 1_000_000
        or not isinstance(exponent, int)
        or exponent < -9
        or (positive and result == 0)
    ):
        raise ValueError("invalid decimal")
    return result


class BudgetConfig(Protocol):
    @property
    def max_requests(self) -> int: ...
    @property
    def max_input_tokens(self) -> int: ...
    @property
    def max_output_tokens(self) -> int: ...
    @property
    def max_cost(self) -> str: ...
    @property
    def input_cost_per_million(self) -> str: ...
    @property
    def output_cost_per_million(self) -> str: ...


@dataclass(frozen=True)
class ConnectivityBudget:
    """One DeepSeek Flash request, using a dated 10x CNY price reservation.

    Rates are not fetched live, and provider billing caps are not enforceable
    here. The caller must verify current pricing before every request.
    """

    max_cost: str
    max_requests: int = field(default=1, init=False)
    max_input_tokens: int = field(default=512, init=False)
    max_output_tokens: int = field(default=256, init=False)
    input_cost_per_million: str = field(default="20", init=False)
    output_cost_per_million: str = field(default="80", init=False)
    model: str = field(default="deepseek-flash", init=False)
    currency: str = field(default="CNY", init=False)
    pricing_version: str = field(default="deepseek-peak-2026-11-17-x10", init=False)

    def __post_init__(self) -> None:
        if _decimal(self.max_cost, positive=True) > Decimal("0.1"):
            raise ValueError("single-call local reservation limit cannot exceed 0.1 CNY")


def _cost(config: BudgetConfig, input_tokens: int, output_tokens: int) -> Decimal:
    # Round each request up, never understate a budget reservation.
    with localcontext() as context:
        context.prec = 50
        raw = (
            Decimal(input_tokens) * _decimal(config.input_cost_per_million, positive=False)
            + Decimal(output_tokens) * _decimal(config.output_cost_per_million, positive=False)
        ) / Decimal(1_000_000)
        return raw.quantize(Decimal("0.000000000001"), rounding=ROUND_CEILING)


def preflight(config: ModelPreflightConfig, *, credential_available: bool) -> dict[str, object]:
    """Report local checks only; caller assertions are not independent isolation proof."""
    return {
        "local_configuration": "VALID" if credential_available else "MISSING_EVAL_CREDENTIAL",
        "network_contacted": False,
        "agent_model_sandbox_enabled": False,
        "single_call_smoke_available": True,
        "external_verification": "REQUIRED",
        "reason": "Provider billing cap, network isolation, test identity and fixture evidence require independent verification; no Agent sandbox adapter is enabled.",
    }


@dataclass(frozen=True)
class Reservation:
    id: int
    input_tokens: int
    output_tokens: int
    cost: Decimal


@dataclass
class BudgetLedger:
    """Conservative sequential reservation ledger, not a provider billing control.

    Reserve the declared maximum possible usage before each request; requests
    never get refunded. A response with unknown, malformed or excess usage burns
    the full reservation and permanently stops further requests. No retries are
    implicit. Caller must establish a trustworthy input-token upper bound.
    """

    config: BudgetConfig
    attempts: int = 0
    input_committed: int = 0
    output_committed: int = 0
    cost_committed: Decimal = Decimal(0)
    stopped: bool = False
    _active: Reservation | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @staticmethod
    def _tokens(value: object) -> TypeGuard[int]:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def reserve(
        self, *, input_tokens_upper_bound: int, output_tokens_upper_bound: int
    ) -> Reservation:
        with self._lock:
            if self.stopped or self._active is not None:
                raise ValueError("ledger stopped or request already active")
            if (
                not self._tokens(input_tokens_upper_bound)
                or not self._tokens(output_tokens_upper_bound)
                or not input_tokens_upper_bound
                or not output_tokens_upper_bound
            ):
                raise ValueError("positive integer token upper bounds required")
            if (
                self.attempts >= self.config.max_requests
                or self.input_committed + input_tokens_upper_bound > self.config.max_input_tokens
                or self.output_committed + output_tokens_upper_bound > self.config.max_output_tokens
            ):
                raise ValueError("request exceeds remaining declared budget")
            cost = _cost(self.config, input_tokens_upper_bound, output_tokens_upper_bound)
            if self.cost_committed + cost > _decimal(self.config.max_cost, positive=True):
                raise ValueError("request exceeds remaining declared budget")
            self.attempts += 1
            reservation = Reservation(
                self.attempts, input_tokens_upper_bound, output_tokens_upper_bound, cost
            )
            self._active = reservation
            return reservation

    def settle(
        self, reservation: Reservation, *, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        with self._lock:
            if reservation is not self._active:
                raise ValueError("unknown or already settled reservation")
            if (
                self._tokens(input_tokens)
                and self._tokens(output_tokens)
                and input_tokens <= reservation.input_tokens
                and output_tokens <= reservation.output_tokens
            ):
                self.input_committed += input_tokens
                self.output_committed += output_tokens
                self.cost_committed += _cost(self.config, input_tokens, output_tokens)
            else:
                self.input_committed += reservation.input_tokens
                self.output_committed += reservation.output_tokens
                self.cost_committed += reservation.cost
                self.stopped = True
            self._active = None
