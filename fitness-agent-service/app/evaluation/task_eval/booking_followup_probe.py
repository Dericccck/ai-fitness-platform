"""Same-session synthetic follow-up after an independently observed booking.

Requires the caller's live disposable services and confirmed fixture. No model
text is retained for review; hashes are not human-reviewable evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from app.infrastructure.model_gateway import ModelResponseError, ModelToolCall, ModelTurn

from .booking_model_boundary import PAYLOAD, PROMPT, _object_without_duplicate_keys
from .budget_store import DurableBudget
from .model_smoke import _validated_usage

FOLLOWUP = "查询我的预约记录，时间范围为 2030 年 1 月 6 日至 1 月 8 日"
SYSTEM_HASH = "899b9acc75d4d6cda1f2a31a9e72d9e5db6872cb1bb82d917e882f657dbf28c1"
TOOLS_HASH = "6b4d58244e35d440f2c6355d6dff02b51f92905ee842910e0a2bb315e2378848"
URL = "https://api.deepseek.com/v1/chat/completions"
RESERVATION_NANO = 102_400_000  # 4096 input at 20 + 256 output at 80 CNY/million
INPUT_LIMIT = 4096
OUTPUT_LIMIT = 256
MAX_BYTES = 65536
_ID = re.compile(r"[0-9a-f]{32}\Z")
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_WINDOW_START = datetime.fromisoformat("2030-01-06T00:00:00+08:00")
_WINDOW_END = datetime.fromisoformat("2030-01-09T00:00:00+08:00")
_BOOKING_START = datetime.fromisoformat(PAYLOAD["start_time"])


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _booking(value: Any, expected_id: str, *, created: bool) -> bool:
    expected_keys = {
        "id",
        "organizationId",
        "userId",
        "coachId",
        "courseId",
        "courseName",
        "startTime",
        "endTime",
        "status",
        "contractId",
    }
    if created:
        expected_keys.add("remainingClassHours")
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    if any(
        value.get(key) != expected
        for key, expected in {
            "id": expected_id,
            "organizationId": "org-1",
            "userId": "student-1",
            "coachId": "coach-1",
            "courseId": "course-1",
            "courseName": "test course",
            "contractId": "contract-1",
        }.items()
    ):
        return False
    if type(value.get("status")) is not int or value["status"] != 1:
        return False
    if created and (
        type(value.get("remainingClassHours")) is not int or value["remainingClassHours"] != 1
    ):
        return False
    try:
        return all(
            datetime.fromisoformat(value[key]) == datetime.fromisoformat(PAYLOAD[target])
            for key, target in (("startTime", "start_time"), ("endTime", "end_time"))
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _json(value: str) -> Any:
    return json.loads(value, object_pairs_hook=_object_without_duplicate_keys)


def _read_argument_issue(args: Any) -> str:
    """Return a fixed reason only; never include model-supplied field values."""
    if not isinstance(args, dict):
        return "ARGUMENTS_NOT_OBJECT"
    if not set(args) <= {"user_id", "limit", "from_time", "to_time"}:
        return "UNEXPECTED_ARGUMENT_FIELD"
    if args.get("user_id") not in (None, "student-1"):
        return "USER_OUTSIDE_FIXTURE"
    if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 100):
        return "LIMIT_OUTSIDE_TOOL_CONTRACT"
    if "from_time" not in args and "to_time" not in args:
        # Preserve and report the service's default-window behavior honestly.
        return "VALID"
    if not isinstance(args.get("from_time"), str) or not isinstance(args.get("to_time"), str):
        return "WINDOW_MISSING_OR_WRONG_TYPE"
    try:
        start = datetime.fromisoformat(args["from_time"])
        end = datetime.fromisoformat(args["to_time"])
        return (
            "VALID"
            if _WINDOW_START <= start <= _BOOKING_START < end <= _WINDOW_END
            else "WINDOW_OUTSIDE_FIXTURE"
        )
    except (TypeError, ValueError):
        return "WINDOW_INVALID_OR_WITHOUT_TIMEZONE"


def _approved_read_arguments(args: Any) -> bool:
    return _read_argument_issue(args) == "VALID"


class FollowupModel:
    """Bounded model adapter; model may request only one permitted read."""

    def __init__(
        self,
        credential: str,
        budget: DurableBudget,
        booking_id: str,
        *,
        transport: httpx.MockTransport | None = None,
        execute: bool = False,
        confirmed_current_pricing: bool = False,
    ):
        if (
            not execute
            or not confirmed_current_pricing
            or not isinstance(credential, str)
            or len(credential.strip()) < 16
            or not isinstance(budget, DurableBudget)
            or not isinstance(booking_id, str)
            or not _ID.fullmatch(booking_id)
            or (transport is not None and type(transport) is not httpx.MockTransport)
        ):
            raise ValueError("explicit fixture, budget and provider authorization required")
        self._credential = credential
        self.budget = budget
        self.booking_id = booking_id
        self.transport = transport
        self.attempts = 0
        self.status = "NOT_CALLED"
        self.requested_read = False
        self.read_validated = False
        self.read_outcome = "NOT_READ"
        self.input_validation_reason = "NOT_CHECKED"
        self.tool_validation_reason = "NOT_CHECKED"
        self.output_sha256: str | None = None
        self._base: list[dict[str, Any]] | None = None
        self._read_call: ModelToolCall | None = None

    def _validate(self, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> bool:
        if _digest(kwargs) != TOOLS_HASH:
            self.input_validation_reason = "TOOLS_SCHEMA_CHANGED"
            return False
        if len(messages) not in (6, 8):
            self.input_validation_reason = "MESSAGE_COUNT_CHANGED"
            return False
        if any(not isinstance(item, dict) for item in messages):
            self.input_validation_reason = "MESSAGE_STRUCTURE_CHANGED"
            return False
        a, b, c, d, e, f = messages[:6]
        if (
            set(a) != {"role", "content"}
            or a.get("role") != "system"
            or not isinstance(a.get("content"), str)
            or hashlib.sha256(a["content"].encode()).hexdigest() != SYSTEM_HASH
            or b != {"role": "user", "content": PROMPT}
            or set(c) != {"role", "content", "tool_calls"}
            or c.get("role") != "assistant"
            or c.get("content") != ""
            or not isinstance(c.get("tool_calls"), list)
            or len(c["tool_calls"]) != 1
            or e != {"role": "assistant", "content": "已完成创建健身预约。"}
            or f != {"role": "user", "content": FOLLOWUP}
        ):
            self.input_validation_reason = "BASE_HISTORY_CHANGED"
            return False
        call = c["tool_calls"][0]
        if (
            not isinstance(call, dict)
            or set(call) != {"id", "type", "function"}
            or call.get("type") != "function"
            or not isinstance(call.get("id"), str)
            or not _CALL_ID.fullmatch(call["id"])
            or call.get("function") != {"name": "fitness_booking_create_v1", "arguments": "{}"}
            or set(d) != {"role", "tool_call_id", "content"}
            or d.get("role") != "tool"
            or d.get("tool_call_id") != call["id"]
            or not isinstance(d.get("content"), str)
        ):
            self.input_validation_reason = "CREATE_CALL_STRUCTURE_CHANGED"
            return False
        try:
            if not _booking(_json(d["content"]), self.booking_id, created=True):
                self.input_validation_reason = "CREATED_BOOKING_MISMATCH"
                return False
            if len(messages) == 6:
                self.input_validation_reason = "VALID" if self.attempts == 0 else "DUPLICATE_CALL"
                return self.attempts == 0
            if self.attempts != 1 or self._base != messages[:6] or self._read_call is None:
                self.input_validation_reason = "FOLLOWUP_STATE_MISMATCH"
                return False
            assistant, tool = messages[6:]
            if assistant != {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": self._read_call.call_id,
                        "type": "function",
                        "function": {
                            "name": "fitness_appointment_list_v1",
                            "arguments": json.dumps(self._read_call.arguments, ensure_ascii=False),
                        },
                    }
                ],
            }:
                self.input_validation_reason = "READ_CALL_MISMATCH"
                return False
            if (
                set(tool) != {"role", "tool_call_id", "content"}
                or tool.get("role") != "tool"
                or tool.get("tool_call_id") != self._read_call.call_id
                or not isinstance(tool.get("content"), str)
            ):
                self.input_validation_reason = "READ_MESSAGE_MISMATCH"
                return False
            result = _json(tool["content"])
            if isinstance(result, list) and len(result) == 0:
                self.read_outcome = "EMPTY"
                self.input_validation_reason = "VALID"
                return True
            if not isinstance(result, list) or len(result) != 1:
                self.input_validation_reason = "READ_RESULT_SHAPE_MISMATCH"
                return False
            if not _booking(result[0], self.booking_id, created=False):
                self.input_validation_reason = "READ_BOOKING_MISMATCH"
                return False
            self.read_validated = True
            self.read_outcome = "MATCHED_FIXTURE"
            self.input_validation_reason = "VALID"
            return True
        except (TypeError, ValueError, KeyError):
            self.input_validation_reason = "MALFORMED_JSON_OR_STRUCTURE"
            return False

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        if self.attempts >= 2:
            self.input_validation_reason = "CALL_LIMIT"
            self.status = "INPUT_NOT_APPROVED"
            raise ModelResponseError(self.status)
        if not self._validate(messages, kwargs):
            self.status = "INPUT_NOT_APPROVED"
            raise ModelResponseError(self.status)
        if len(json.dumps(messages, ensure_ascii=False).encode()) > INPUT_LIMIT:
            self.input_validation_reason = "INPUT_TOO_LARGE"
            self.status = "INPUT_NOT_APPROVED"
            raise ModelResponseError(self.status)
        if self._credential in str(messages):
            self.input_validation_reason = "SENSITIVE_INPUT"
            self.status = "INPUT_NOT_APPROVED"
            raise ModelResponseError(self.status)
        request_id = uuid4().hex
        try:
            self.budget.reserve(request_id, RESERVATION_NANO)
        except ValueError as exc:
            self.status = "BUDGET_UNAVAILABLE"
            raise ModelResponseError(self.status) from exc
        self.attempts += 1
        self.status = "MODEL_CALL_UNCERTAIN"
        verified = False
        turn: ModelTurn | None = None
        try:
            body = {
                "model": "deepseek-flash",
                "messages": messages,
                "tools": kwargs["tools"],
                "max_tokens": OUTPUT_LIMIT,
                "temperature": 0,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
            async with (
                httpx.AsyncClient(
                    transport=self.transport, trust_env=False, follow_redirects=False, timeout=20
                ) as client,
                client.stream(
                    "POST", URL, headers={"Authorization": f"Bearer {self._credential}"}, json=body
                ) as response,
            ):
                if response.status_code != 200:
                    raise ModelResponseError("PROVIDER_HTTP_ERROR")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ModelResponseError("RESPONSE_TOO_LARGE")
            if self._credential.encode() in raw:
                raise ModelResponseError("SENSITIVE_OUTPUT")
            payload = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
            if not isinstance(payload, dict):
                raise ModelResponseError("INVALID_RESPONSE")
            inp, out = _validated_usage(payload.get("usage"), deepseek=True)
            if (
                inp is None
                or out is None
                or not 0 < inp <= INPUT_LIMIT
                or not 0 < out <= OUTPUT_LIMIT
            ):
                raise ModelResponseError("USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION")
            choices = payload.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                raise ModelResponseError("INVALID_RESPONSE")
            choice = choices[0]
            message = choice.get("message")
            usage = payload.get("usage")
            details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            if (
                not isinstance(message, dict)
                or message.get("reasoning_content")
                or message.get("server_tool_calls")
                or (isinstance(details, dict) and details.get("reasoning_tokens"))
            ):
                raise ModelResponseError("UNEXPECTED_REASONING_OR_TOOL_CALL")
            reason = choice.get("finish_reason")
            content = message.get("content")
            calls = message.get("tool_calls")
            if reason == "stop" and isinstance(content, str) and content.strip() and not calls:
                verified = True
                self.output_sha256 = hashlib.sha256(content.encode()).hexdigest()
                self.status = (
                    "DIRECT_ANSWER"
                    if self.attempts == 1
                    else "ANSWER_AFTER_EMPTY_READ"
                    if self.read_outcome == "EMPTY"
                    else "ANSWER_AFTER_READ"
                )
                turn = ModelTurn(
                    content=content, tool_calls=(), input_tokens=inp, output_tokens=out
                )
            elif (
                reason == "tool_calls"
                and self.attempts == 1
                and content in (None, "")
                and isinstance(calls, list)
                and len(calls) == 1
                and isinstance(calls[0], dict)
            ):
                verified = True
                call = calls[0]
                fn = call.get("function")
                call_id = call.get("id")
                if (
                    call.get("type") != "function"
                    or not isinstance(call_id, str)
                    or not _CALL_ID.fullmatch(call_id)
                    or not isinstance(fn, dict)
                    or fn.get("name") != "fitness_appointment_list_v1"
                    or not isinstance(fn.get("arguments"), str)
                ):
                    self.tool_validation_reason = "CALL_STRUCTURE_OR_NAME_INVALID"
                    raise ModelResponseError("UNAPPROVED_TOOL_REQUEST")
                try:
                    args = _json(fn["arguments"])
                except (TypeError, ValueError) as exc:
                    self.tool_validation_reason = "ARGUMENTS_INVALID_JSON"
                    raise ModelResponseError("UNAPPROVED_TOOL_REQUEST") from exc
                self.tool_validation_reason = _read_argument_issue(args)
                if self.tool_validation_reason != "VALID":
                    raise ModelResponseError("UNAPPROVED_TOOL_REQUEST")
                self._read_call = ModelToolCall(
                    call_id=call_id, name="fitness_appointment_list_v1", arguments=args
                )
                self._base = messages[:6]
                self.requested_read = True
                self.status = "APPROVED_READ_REQUEST"
                turn = ModelTurn(
                    content="", tool_calls=(self._read_call,), input_tokens=inp, output_tokens=out
                )
            else:
                raise ModelResponseError("MODEL_DID_NOT_FINISH_NORMALLY")
        except ModelResponseError as exc:
            self.status = str(exc)
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            self.status = "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
        finally:
            self.budget.finish(
                request_id,
                usage_known=verified,
                reason_code="UNSPECIFIED" if verified else self.status,
            )
        if turn is None:
            raise ModelResponseError(self.status)
        return turn

    def observation(self) -> dict[str, Any]:
        return {
            "component_status": "OBSERVED"
            if self.status in {"DIRECT_ANSWER", "ANSWER_AFTER_READ"}
            else "INCOMPLETE",
            "task_status": "UNKNOWN",
            "status": self.status,
            "attempts": self.attempts,
            "requested_read": self.requested_read,
            "read_result_validated": self.read_validated,
            "read_outcome": self.read_outcome,
            "input_validation_reason": self.input_validation_reason,
            "tool_validation_reason": self.tool_validation_reason,
            "output_sha256": self.output_sha256,
            "human_review": "NOT_VERIFIED",
            "raw_output_retained": False,
            "raw_output_human_reviewable": False,
            "limitations": [
                "Output text was not retained because safe redaction cannot be guaranteed; the hash is not reviewable evidence.",
                "Synthetic session and disposable services only; provider spending cap is not verified.",
            ],
        }
