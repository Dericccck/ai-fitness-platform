"""One budgeted real-model turn for five frozen synthetic clarification cases."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

import httpx

from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from .booking_model_boundary import (
    INPUT_LIMIT,
    MAX_RESPONSE_BYTES,
    MODEL,
    OUTPUT_LIMIT,
    RESERVATION_NANO,
    URL,
    _object_without_duplicate_keys,
)
from .budget_store import DurableBudget
from .model_smoke import _validated_usage

INPUT_HASHES = {
    "BOOK-011": "ec29621cab57610b83d1e59be3e9800e3a32250a94ea029e74e9e571fc4ba721",
    "BOOK-012": "c77626c6dde34fd44c64f0d97a1065264a5bcfaf961bef45a2dd4473c48d9948",
    "BOOK-013": "dd301af7741c9d410e14fe8dd6cba63edd313867e3eeaf507b14556356487335",
    "BOOK-014": "b7b92bfa90e7674931fa5faada821a0336525521bf38bb7bfe8f84b77ed575e2",
    "BOOK-015": "c890913651c878a1dabbba455f3df675d45da66d453299a9c5a2aa205a535fea",
}
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class ClarificationProbeError(RuntimeError):
    pass


class OneClarificationModel:
    """Replays the Supervisor's actual choice, including a forced tool if present.

    Only the exact synthetic case and tool schema identified by a frozen digest
    may leave the machine. A second model call is rejected, not mocked.
    """

    def __init__(
        self,
        case_id: str,
        credential: str,
        budget: DurableBudget,
        *,
        transport: httpx.MockTransport | None = None,
    ) -> None:
        if (
            case_id not in INPUT_HASHES
            or not isinstance(credential, str)
            or len(credential.strip()) < 16
            or not isinstance(budget, DurableBudget)
            or (transport is not None and type(transport) is not httpx.MockTransport)
        ):
            raise ValueError("invalid clarification model configuration")
        self.case_id = case_id
        self._credential = credential
        self.budget = budget
        self.transport = transport
        self.attempts = 0
        self.reason = "NOT_CALLED"
        self.response_sha256: str | None = None
        self.forced_tool: str | None = None
        self.tool_count = 0
        self.usage: tuple[int | None, int | None] = (None, None)
        self.last_arguments: dict[str, Any] | None = None

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        raw_input = json.dumps(
            {"messages": messages, **kwargs},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if (
            self.attempts
            or hashlib.sha256(raw_input).hexdigest() != INPUT_HASHES[self.case_id]
            or len(raw_input) > INPUT_LIMIT
            or self._credential.encode() in raw_input
        ):
            self.reason = "UNAPPROVED_INPUT_OR_REPEAT"
            raise ClarificationProbeError(self.reason)
        tools = kwargs.get("tools")
        forced = kwargs.get("force_tool_name")
        if not isinstance(tools, list) or forced != "fitness_booking_create_v1":
            self.reason = "UNAPPROVED_TOOLS"
            raise ClarificationProbeError(self.reason)
        self.forced_tool = forced
        request_id = uuid4().hex
        self.budget.reserve(request_id, RESERVATION_NANO)
        self.attempts += 1
        self.reason = "MODEL_CALL_UNCERTAIN"
        valid = False
        try:
            body = {
                "model": MODEL,
                "messages": messages,
                "tools": tools,
                "tool_choice": {"type": "function", "function": {"name": forced}},
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
                    raise ClarificationProbeError("PROVIDER_HTTP_ERROR")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ClarificationProbeError("RESPONSE_TOO_LARGE")
            if self._credential.encode() in raw:
                raise ClarificationProbeError("SENSITIVE_OUTPUT")
            value = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
            if not isinstance(value, dict):
                raise ClarificationProbeError("INVALID_RESPONSE")
            inp, out = _validated_usage(value.get("usage"), deepseek=True)
            if (
                inp is None
                or out is None
                or not 0 < inp <= INPUT_LIMIT
                or not 0 < out <= OUTPUT_LIMIT
            ):
                raise ClarificationProbeError("USAGE_UNAVAILABLE")
            usage = value.get("usage")
            details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            if isinstance(details, dict) and details.get("reasoning_tokens"):
                raise ClarificationProbeError("UNEXPECTED_REASONING")
            choices = value.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                raise ClarificationProbeError("INVALID_RESPONSE")
            choice = choices[0]
            message = choice.get("message")
            if (
                choice.get("finish_reason") != "tool_calls"
                or not isinstance(message, dict)
                or message.get("reasoning_content")
                or message.get("server_tool_calls")
            ):
                raise ClarificationProbeError("UNEXPECTED_MODEL_RESPONSE")
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
                raise ClarificationProbeError("INVALID_TOOL_CALL")
            call = calls[0]
            function = call.get("function")
            call_id = call.get("id")
            if (
                call.get("type") != "function"
                or not isinstance(call_id, str)
                or _CALL_ID.fullmatch(call_id) is None
                or not isinstance(function, dict)
                or function.get("name") != forced
                or not isinstance(function.get("arguments"), str)
            ):
                raise ClarificationProbeError("INVALID_TOOL_CALL")
            arguments = json.loads(
                function["arguments"], object_pairs_hook=_object_without_duplicate_keys
            )
            if not isinstance(arguments, dict):
                raise ClarificationProbeError("INVALID_TOOL_ARGUMENTS")
            content = message.get("content") or ""
            if not isinstance(content, str):
                raise ClarificationProbeError("INVALID_RESPONSE")
            self.response_sha256 = hashlib.sha256(raw).hexdigest()
            self.usage = (inp, out)
            self.tool_count = 1
            self.last_arguments = arguments
            self.reason = "MODEL_RESPONSE_RECEIVED"
            valid = True
            return ModelTurn(
                content=content,
                tool_calls=(ModelToolCall(call_id, forced, arguments),),
                input_tokens=inp,
                output_tokens=out,
            )
        except ClarificationProbeError as exc:
            self.reason = str(exc)
            raise
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError) as exc:
            self.reason = "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
            raise ClarificationProbeError(self.reason) from exc
        finally:
            self.budget.finish(
                request_id, usage_known=valid, reason_code="UNSPECIFIED" if valid else self.reason
            )
