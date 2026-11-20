"""Single paid model call for the synthetic, isolated booking fixture.

The caller supplies a dedicated credential and a durable CNY ledger. No secret
is read here. Normal production model settings, retries and proxy configuration
are deliberately not used. The request is fixed to one frozen synthetic
message and an exact frozen tool schema; any drift stops before network access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from app.agent.fitness_tools import BookingCreateToolInput
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from .budget_store import MAX_LIMIT_NANO, DurableBudget
from .model_safety import BudgetLedger
from .model_smoke import _validated_usage

PROMPT = (
    "请预约 org-1 的 student-1 使用 contract-1，coach-1 的 course-1，"
    "2030-01-07 09:00 至 10:00（东八区）。"
)
PAYLOAD = {
    "organization_id": "org-1",
    "student_id": "student-1",
    "contract_id": "contract-1",
    "coach_id": "coach-1",
    "course_id": "course-1",
    "start_time": "2030-01-07T09:00:00+08:00",
    "end_time": "2030-01-07T10:00:00+08:00",
}
MODEL = "deepseek-flash"
URL = "https://api.deepseek.com/v1/chat/completions"
INPUT_HASH = "bd142ddce279fe336ddff1e1fc1775e1a2cf70c862f9beb3541edbc87ca83254"
BOOK_018_PROMPT = (
    "请预约 org-1 的 student-1 使用 contract-1，coach-1 的 course-1，"
    "2030-01-01 10:00 至 11:00（东八区）。"
)
BOOK_018_INPUT_HASH = "61c0d03f474d436cf5515d4c26e1e470d47c95a055d209f0c1edbfc0914d72e2"
INPUT_LIMIT = 8192
OUTPUT_LIMIT = 512
RESERVATION_NANO = 204_800_000
MAX_RESPONSE_BYTES = 65_536
_ARGUMENT_KEYS = frozenset(
    {"studentId", "contractId", "coachId", "courseId", "startTime", "endTime"}
)
_FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
_REASONS = frozenset(
    {
        "NOT_CALLED",
        "UNAPPROVED_MODEL_INPUT_OR_TOOLS",
        "CALL_LIMIT_OR_BUDGET_EXHAUSTED",
        "RESERVATION_MISMATCH",
        "DURABLE_BUDGET_UNAVAILABLE",
        "MODEL_CALL_UNCERTAIN",
        "PROVIDER_HTTP_ERROR",
        "RESPONSE_TOO_LARGE",
        "SENSITIVE_OUTPUT",
        "INVALID_RESPONSE",
        "MISSING_TOOL_CALL",
        "UNEXPECTED_REASONING_OR_SERVER_TOOL",
        "INVALID_TOOL_CALL",
        "INVALID_TOOL_ARGUMENTS",
        "FIXTURE_PARAMETERS_MISMATCH",
        "MODEL_TIMEOUT",
        "MODEL_TRANSPORT_ERROR",
        "MODEL_RESPONSE_OR_TRANSPORT_ERROR",
        "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION",
        "MODEL_RESPONSE_RECEIVED",
    }
)


@dataclass(frozen=True)
class _Budget:
    max_cost: str = field(default="0.5", init=False)
    max_requests: int = field(default=1, init=False)
    max_input_tokens: int = field(default=INPUT_LIMIT, init=False)
    max_output_tokens: int = field(default=OUTPUT_LIMIT, init=False)
    input_cost_per_million: str = field(default="20", init=False)
    output_cost_per_million: str = field(default="80", init=False)


class BookingModelProbeError(RuntimeError):
    """The model call or returned synthetic booking could not be trusted."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class _Profile:
    name: str
    input_hash: str
    payload_items: tuple[tuple[str, str], ...]


_DEFAULT_PROFILE = _Profile("default", INPUT_HASH, tuple(PAYLOAD.items()))
_BOOK_018_PROFILE = _Profile(
    "BOOK-018",
    BOOK_018_INPUT_HASH,
    (
        ("organization_id", "org-1"),
        ("student_id", "student-1"),
        ("contract_id", "contract-1"),
        ("coach_id", "coach-1"),
        ("course_id", "course-1"),
        ("start_time", "2030-01-01T10:00:00+08:00"),
        ("end_time", "2030-01-01T11:00:00+08:00"),
    ),
)


_BOOK_004_PROFILE = _Profile(
    "BOOK-004",
    "601551d67f5ff6619ccf094489b4e3b4ec27649a5bcc1807335a24500ba1f3ca",
    _BOOK_018_PROFILE.payload_items,
)


def _profile(name: str) -> _Profile:
    if name == "default":
        return _DEFAULT_PROFILE
    if name == "BOOK-018":
        return _BOOK_018_PROFILE
    if name == "BOOK-004":
        return _BOOK_004_PROFILE
    raise ValueError("unsupported booking model profile")


def expected_payload(value: Any, *, profile: str = "default") -> bool:
    """Only the explicitly approved fixture may proceed to confirmation."""
    expected = _profile(profile)
    if not isinstance(value, dict):
        return False
    try:
        actual = BookingCreateToolInput.model_validate(value)
        fixed = BookingCreateToolInput.model_validate(dict(expected.payload_items))
        return actual.model_dump() == fixed.model_dump()
    except ValueError:
        return False


class OneBookingModel:
    """One provider call, reserved before sending, without retries or redirects."""

    def __init__(
        self,
        credential: str,
        budget: DurableBudget,
        *,
        transport: httpx.MockTransport | None = None,
        execute: bool = False,
        confirmed_current_pricing: bool = False,
        profile: str = "default",
    ) -> None:
        if type(profile) is not str:
            raise ValueError("unsupported booking model profile")
        self._profile = _profile(profile)
        if not execute or not confirmed_current_pricing:
            raise ValueError("explicit execution and current pricing confirmation are required")
        if not isinstance(credential, str) or len(credential.strip()) < 16:
            raise ValueError("dedicated evaluation credential is required")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only MockTransport is supported as an injected transport")
        if not isinstance(budget, DurableBudget) or budget.limit_nano > MAX_LIMIT_NANO:
            raise ValueError("a valid persistent CNY budget is required")
        self._credential = credential
        self._transport = transport
        self.budget = budget
        self.ledger = BudgetLedger(_Budget())
        self.reason = "NOT_CALLED"
        self.usage: tuple[int | None, int | None] = (None, None)
        self.http_status: int | None = None
        self.response_hash: str | None = None
        self.finish_reason: str | None = None
        self.argument_missing: list[str] | None = None
        self.argument_extra_count: int | None = None
        self.argument_differing: list[str] | None = None
        self.argument_mark_state: str | None = None

    @property
    def profile(self) -> str:
        """The chosen fixed profile is immutable through the public API."""
        return self._profile.name

    def diagnostics(self) -> dict[str, Any]:
        """Only return bounded counters and predefined status labels; no provider data."""
        reason = (
            self.reason
            if isinstance(self.reason, str) and self.reason in _REASONS
            else "UNCLASSIFIED_MODEL_FAILURE"
        )
        input_tokens, output_tokens = self.usage
        valid_usage = (
            type(input_tokens) is int
            and type(output_tokens) is int
            and 0 <= input_tokens <= INPUT_LIMIT
            and 0 <= output_tokens <= OUTPUT_LIMIT
        )
        try:
            snapshot = self.budget.snapshot()
            reserved = snapshot.get("reserved_nano")
            pending = snapshot.get("pending")
            frozen = snapshot.get("frozen")
            if (
                type(reserved) is not int
                or not 0 <= reserved <= self.budget.limit_nano <= MAX_LIMIT_NANO
                or type(pending) is not int
                or pending < 0
                or type(frozen) is not bool
            ):
                raise ValueError("invalid local budget state")
            budget: dict[str, Any] = {
                "status": "AVAILABLE",
                "reserved_nano": reserved,
                "pending": pending,
                "frozen": frozen,
            }
        except Exception:  # noqa: BLE001 - do not disclose local path or database errors
            budget = {"status": "UNAVAILABLE"}
        return {
            "reason": reason,
            "attempts": self.ledger.attempts if self.ledger.attempts in (0, 1) else None,
            "http_status": self.http_status
            if type(self.http_status) is int and 100 <= self.http_status <= 599
            else None,
            "reported_input_tokens": input_tokens if valid_usage else None,
            "reported_output_tokens": output_tokens if valid_usage else None,
            "finish_reason": self.finish_reason,
            "argument_missing": self.argument_missing,
            "argument_extra_count": self.argument_extra_count,
            "argument_differing": self.argument_differing,
            "argument_mark_state": self.argument_mark_state,
            "usage_status": "VALIDATED_PROVIDER_REPORTED"
            if valid_usage
            else "UNKNOWN_OR_EXCEEDS_RESERVATION",
            "budget": budget,
            "provider_cap_verified": False,
        }

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        raw_input = json.dumps(
            {"messages": messages, **kwargs},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if (
            hashlib.sha256(raw_input).hexdigest() != self._profile.input_hash
            or len(raw_input) > INPUT_LIMIT
            or self._credential.encode() in raw_input
        ):
            self.reason = "UNAPPROVED_MODEL_INPUT_OR_TOOLS"
            raise BookingModelProbeError(self.reason)
        try:
            reservation = self.ledger.reserve(
                input_tokens_upper_bound=INPUT_LIMIT, output_tokens_upper_bound=OUTPUT_LIMIT
            )
        except ValueError as exc:
            self.reason = "CALL_LIMIT_OR_BUDGET_EXHAUSTED"
            raise BookingModelProbeError(self.reason) from exc
        if reservation.cost != Decimal("0.2048"):
            self.reason = "RESERVATION_MISMATCH"
            raise BookingModelProbeError(self.reason)
        request_id = uuid4().hex
        try:
            self.budget.reserve(request_id, RESERVATION_NANO)
        except ValueError as exc:
            self.reason = "DURABLE_BUDGET_UNAVAILABLE"
            raise BookingModelProbeError(self.reason) from exc
        self.reason = "MODEL_CALL_UNCERTAIN"
        input_tokens = output_tokens = None
        selected: ModelToolCall | None = None
        try:
            request_body = {
                "model": MODEL,
                "messages": messages,
                "tools": kwargs["tools"],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "fitness_booking_create_v1"},
                },
                "max_tokens": OUTPUT_LIMIT,
                "temperature": 0,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
            async with (
                httpx.AsyncClient(
                    transport=self._transport, trust_env=False, follow_redirects=False, timeout=20
                ) as client,
                client.stream(
                    "POST",
                    URL,
                    headers={"Authorization": f"Bearer {self._credential}"},
                    json=request_body,
                ) as response,
            ):
                self.http_status = response.status_code
                if response.status_code != 200:
                    raise BookingModelProbeError("PROVIDER_HTTP_ERROR")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise BookingModelProbeError("RESPONSE_TOO_LARGE")
            data = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
            if self._credential.encode() in raw:
                raise BookingModelProbeError("SENSITIVE_OUTPUT")
            if not isinstance(data, dict):
                raise BookingModelProbeError("INVALID_RESPONSE")
            input_tokens, output_tokens = _validated_usage(data.get("usage"), deepseek=True)
            if (
                input_tokens is not None
                and output_tokens is not None
                and input_tokens <= INPUT_LIMIT
                and output_tokens <= OUTPUT_LIMIT
            ):
                self.usage = input_tokens, output_tokens
            choices = data.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                raise BookingModelProbeError("INVALID_RESPONSE")
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            self.finish_reason = (
                finish_reason
                if isinstance(finish_reason, str) and finish_reason in _FINISH_REASONS
                else None
            )
            message = choice.get("message")
            if choice.get("finish_reason") != "tool_calls" or not isinstance(message, dict):
                raise BookingModelProbeError("MISSING_TOOL_CALL")
            usage = data.get("usage")
            details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            if (
                message.get("reasoning_content")
                or message.get("server_tool_calls")
                or (isinstance(details, dict) and details.get("reasoning_tokens"))
            ):
                raise BookingModelProbeError("UNEXPECTED_REASONING_OR_SERVER_TOOL")
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
                raise BookingModelProbeError("INVALID_TOOL_CALL")
            call = calls[0]
            function = call.get("function")
            call_id = call.get("id")
            if (
                call.get("type") != "function"
                or not isinstance(call_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", call_id)
                or not isinstance(function, dict)
                or function.get("name") != "fitness_booking_create_v1"
                or not isinstance(function.get("arguments"), str)
            ):
                raise BookingModelProbeError("INVALID_TOOL_CALL")
            try:
                arguments = json.loads(
                    function["arguments"], object_pairs_hook=_object_without_duplicate_keys
                )
            except (ValueError, TypeError) as exc:
                raise BookingModelProbeError("INVALID_TOOL_ARGUMENTS") from exc
            if not isinstance(arguments, dict):
                raise BookingModelProbeError("INVALID_TOOL_ARGUMENTS")
            if "mark" not in arguments:
                self.argument_mark_state = "ABSENT"
            elif arguments["mark"] is None:
                self.argument_mark_state = "NULL"
            elif type(arguments["mark"]) is int and arguments["mark"] in (0, 1):
                self.argument_mark_state = "ALLOWED_INTEGER"
            else:
                self.argument_mark_state = "OTHER"
            self.argument_missing = sorted(_ARGUMENT_KEYS.difference(arguments))
            # The production schema exposes optional mark=None. It is exactly
            # equivalent to omitting the field in this frozen fixture; other
            # mark values still fail the payload equality check below.
            self.argument_extra_count = len(set(arguments).difference(_ARGUMENT_KEYS | {"mark"}))
            if self.argument_missing or self.argument_extra_count:
                raise BookingModelProbeError("INVALID_TOOL_ARGUMENTS")
            arguments["organizationId"] = dict(self._profile.payload_items)["organization_id"]
            if not expected_payload(arguments, profile=self.profile):
                # Export only fixed schema names, never untrusted values or keys.
                expected = BookingCreateToolInput.model_validate(
                    dict(self._profile.payload_items)
                ).model_dump(by_alias=True)
                try:
                    actual = BookingCreateToolInput.model_validate(arguments).model_dump(
                        by_alias=True
                    )
                except ValueError:
                    self.argument_differing = None
                else:
                    self.argument_differing = sorted(
                        key for key in (_ARGUMENT_KEYS | {"mark"}) if actual[key] != expected[key]
                    )
                raise BookingModelProbeError("FIXTURE_PARAMETERS_MISMATCH")
            arguments.pop("organizationId")
            selected = ModelToolCall(
                call_id=call_id, name="fitness_booking_create_v1", arguments=arguments
            )
            self.response_hash = hashlib.sha256(raw).hexdigest()
        except BookingModelProbeError as exc:
            reason = str(exc)
            self.reason = reason if reason in _REASONS else "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
        except httpx.TimeoutException:
            self.reason = "MODEL_TIMEOUT"
        except httpx.HTTPError:
            self.reason = "MODEL_TRANSPORT_ERROR"
        except (ValueError, TypeError, UnicodeError):
            self.reason = "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
        finally:
            self.ledger.settle(reservation, input_tokens=input_tokens, output_tokens=output_tokens)
            if self.ledger.stopped and self.reason == "MODEL_CALL_UNCERTAIN":
                self.reason = "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
            self.budget.finish(
                request_id,
                usage_known=(
                    not self.ledger.stopped
                    and selected is not None
                    and self.reason == "MODEL_CALL_UNCERTAIN"
                ),
                reason_code=self.reason if self.reason != "MODEL_CALL_UNCERTAIN" else "UNSPECIFIED",
            )
        if self.ledger.stopped and self.reason == "MODEL_CALL_UNCERTAIN":
            self.reason = "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
        if self.reason != "MODEL_CALL_UNCERTAIN" or selected is None:
            raise BookingModelProbeError(self.reason)
        self.reason = "MODEL_RESPONSE_RECEIVED"
        self.usage = input_tokens, output_tokens
        return ModelTurn(
            content="",
            tool_calls=(selected,),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
