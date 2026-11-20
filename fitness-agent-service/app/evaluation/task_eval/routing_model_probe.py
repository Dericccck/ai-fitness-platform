"""Frozen synthetic development routing observations using a budgeted model.

No business tools are supplied or executed. Task outcomes and draft labels are
not certified by these component-only observations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.agent.routing import (
    HybridIntentRouter,
    _ModelRouteDecision,
    _validate_model_intent,
    router_system_prompt,
)
from app.infrastructure.model_gateway import JsonModelTurn, ModelGateway, ModelResponseError

from .budget_store import DurableBudget
from .dataset import Dataset, load_dataset
from .model_smoke import _validated_usage
from .routing_intent_cases import _CASES, _bound

SYSTEM_SHA256 = "ea1e206eef0ea3f417f42f1ddc8ec273c4e57e7ef89d05405465492d43999df8"
MODEL = "deepseek-flash"
URL = "https://api.deepseek.com/v1/chat/completions"
INPUT_LIMIT = 4096
OUTPUT_LIMIT = 220
RESERVATION_NANO = 100_000_000  # 4096 input at 20 + 220 output at 80 CNY/million, rounded up
MAX_RESPONSE_BYTES = 65536
DEV_IDS = tuple(key for key, value in _CASES.items() if value[0] == "dev")


class _RouterModel:
    def __init__(
        self,
        credential: str,
        budget: DurableBudget,
        transport: httpx.MockTransport | None,
        max_calls: int,
        *,
        supervisor_context: bool = False,
    ):
        self.credential = credential
        self.budget = budget
        self.transport = transport
        self.max_calls = max_calls
        self.supervisor_context = supervisor_context
        self.attempts = 0
        self.expected_message = ""
        self.last_status = "NOT_CALLED"
        self.last_usage: tuple[int, int] | None = None
        self.last_schema_errors: list[dict[str, str]] | None = None

    async def chat_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> JsonModelTurn:
        expected = [
            {"role": "system", "content": router_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "previous_route": None,
                        "recent_messages": (
                            [{"role": "user", "content": self.expected_message}]
                            if self.supervisor_context
                            else []
                        ),
                        "current_message": self.expected_message,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        if (
            messages != expected
            or hashlib.sha256(expected[0]["content"].encode()).hexdigest() != SYSTEM_SHA256
            or max_output_tokens != OUTPUT_LIMIT
            or temperature != 0
            or self.credential in str(expected)
            or len(json.dumps(expected, ensure_ascii=False).encode()) > INPUT_LIMIT
        ):
            self.last_status = "INPUT_NOT_APPROVED"
            raise ModelResponseError(self.last_status)
        if self.attempts >= self.max_calls:
            self.last_status = "CALL_LIMIT"
            raise ModelResponseError(self.last_status)
        self.last_usage = None
        request_id = uuid4().hex
        try:
            self.budget.reserve(request_id, RESERVATION_NANO)
        except ValueError as exc:
            self.last_status = "BUDGET_UNAVAILABLE"
            raise ModelResponseError(self.last_status) from exc
        self.attempts += 1
        self.last_status = "MODEL_CALL_UNCERTAIN"
        verified = False
        result: JsonModelTurn | None = None
        try:
            body = {
                "model": MODEL,
                "messages": expected,
                "temperature": 0,
                "max_tokens": OUTPUT_LIMIT,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
                "stream": False,
            }
            async with (
                httpx.AsyncClient(
                    transport=self.transport, trust_env=False, follow_redirects=False, timeout=20
                ) as client,
                client.stream(
                    "POST", URL, headers={"Authorization": f"Bearer {self.credential}"}, json=body
                ) as response,
            ):
                if response.status_code != 200:
                    raise ModelResponseError("PROVIDER_HTTP_ERROR")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ModelResponseError("RESPONSE_TOO_LARGE")
            if self.credential.encode() in raw:
                raise ModelResponseError("SENSITIVE_OUTPUT")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ModelResponseError("INVALID_RESPONSE")
            inp, out = _validated_usage(data.get("usage"), deepseek=True)
            if (
                inp is None
                or out is None
                or not 0 < inp <= INPUT_LIMIT
                or not 0 < out <= OUTPUT_LIMIT
            ):
                raise ModelResponseError("USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION")
            choices = data.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                raise ModelResponseError("INVALID_RESPONSE")
            choice = choices[0]
            msg = choice.get("message")
            if not isinstance(msg, dict) or choice.get("finish_reason") != "stop":
                raise ModelResponseError("MODEL_DID_NOT_FINISH_NORMALLY")
            if (
                msg.get("tool_calls")
                or msg.get("server_tool_calls")
                or msg.get("reasoning_content")
            ):
                raise ModelResponseError("UNEXPECTED_REASONING_OR_TOOL_CALL")
            usage = data.get("usage")
            details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            if isinstance(details, dict) and details.get("reasoning_tokens"):
                raise ModelResponseError("UNEXPECTED_REASONING_OR_TOOL_CALL")
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ModelResponseError("MODEL_OUTPUT_MISSING")
            try:
                parsed = _ModelRouteDecision.model_validate_json(content)
                self.last_schema_errors = []
                try:
                    for item in parsed.intents:
                        _validate_model_intent(item)
                except ValueError:
                    self.last_schema_errors.append({"field": "intents", "error": "INVALID_MAPPING"})
            except ValidationError as exc:
                # Pydantic locations for extra fields can contain arbitrary
                # provider text; emit only fixed field names and error types.
                known_fields = {"intents", "route", "intent", "confidence", "reason_code"}
                known_errors = {
                    "missing",
                    "extra_forbidden",
                    "too_short",
                    "too_long",
                    "string_type",
                    "string_pattern_mismatch",
                    "float_parsing",
                    "float_type",
                    "list_type",
                    "json_invalid",
                    "model_type",
                    "less_than_equal",
                    "greater_than_equal",
                }
                self.last_schema_errors = []
                for issue in exc.errors(include_input=False, include_url=False)[:8]:
                    location = issue.get("loc", ())
                    fixed = [
                        str(item)
                        for item in location
                        if isinstance(item, str) and item in known_fields
                    ]
                    error_type = issue.get("type")
                    self.last_schema_errors.append(
                        {
                            "field": ".".join(fixed) if fixed else "OTHER",
                            "error": error_type if error_type in known_errors else "OTHER",
                        }
                    )
            result = JsonModelTurn(content=content, input_tokens=inp, output_tokens=out)
            self.last_usage = (inp, out)
            verified = True
            self.last_status = "MODEL_RESPONSE_RECEIVED"
        except ModelResponseError as exc:
            self.last_status = str(exc)
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            self.last_status = "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
        finally:
            self.budget.finish(
                request_id,
                usage_known=verified,
                reason_code="UNSPECIFIED" if verified else self.last_status,
            )
        if result is None:
            raise ModelResponseError(self.last_status)
        return result


async def evaluate_dev_routing_model(
    dataset: Dataset,
    *,
    credential: str,
    budget: DurableBudget,
    max_calls: int,
    execute: bool = False,
    confirmed_current_pricing: bool = False,
    transport: httpx.MockTransport | None = None,
) -> dict[str, Any]:
    """Evaluate exact dev cases sequentially; caller explicitly opts into provider calls."""
    if (
        not execute
        or not confirmed_current_pricing
        or not isinstance(credential, str)
        or len(credential.strip()) < 16
        or not isinstance(budget, DurableBudget)
        or type(max_calls) is not int
        or not 1 <= max_calls <= len(DEV_IDS)
        or (transport is not None and type(transport) is not httpx.MockTransport)
    ):
        raise ValueError(
            "explicit execution, bounded calls, credential and existing budget required"
        )
    # Preflight the complete set before any external calls.
    by_id = {case.case_id: case for case in dataset.cases if case.case_id in DEV_IDS}
    if len(by_id) != len(DEV_IDS) or any(not _bound(by_id[key], _CASES[key]) for key in DEV_IDS):
        raise ValueError("frozen development case set changed")
    if hashlib.sha256(router_system_prompt().encode()).hexdigest() != SYSTEM_SHA256:
        raise ValueError("router system prompt changed")
    if budget.snapshot()["frozen"] or budget.snapshot()["pending"]:
        raise ValueError("budget is frozen or pending")
    model = _RouterModel(credential, budget, transport, max_calls)
    router = HybridIntentRouter(cast(ModelGateway, model), timeout_seconds=30)
    rows: list[dict[str, Any]] = []
    for case_id in DEV_IDS:
        snapshot = budget.snapshot()
        if snapshot["frozen"] or snapshot["pending"]:
            rows.append(
                {
                    "case_id": case_id,
                    "task_status": "UNKNOWN",
                    "component_status": "SKIPPED",
                    "reason": "BUDGET_FROZEN_OR_PENDING",
                }
            )
            continue
        model.expected_message = _CASES[case_id][2]
        model.last_status = "NOT_CALLED"
        model.last_usage = None
        model.last_schema_errors = None
        try:
            decision = await router.decide(
                model.expected_message, recent_messages=[], previous_route=None
            )
            rows.append(
                {
                    "case_id": case_id,
                    "task_status": "UNKNOWN",
                    # The router can catch a failed provider call and return a
                    # safe fallback; do not call that a model observation.
                    "component_status": "OBSERVED"
                    if model.last_status in {"NOT_CALLED", "MODEL_RESPONSE_RECEIVED"}
                    else "INCONCLUSIVE",
                    "route_source": decision.source,
                    "fallback_reason": decision.reason_code
                    if decision.source == "FALLBACK"
                    and decision.reason_code
                    in {
                        "LOW_CONFIDENCE",
                        "MODEL_UNCERTAIN",
                        "ROUTER_UNAVAILABLE",
                        "TOO_MANY_INTENTS",
                        "MULTIPLE_WRITE_INTENTS",
                    }
                    else None,
                    "intents": [
                        {
                            "route": intent.route,
                            "intent": intent.intent,
                            "is_write": intent.is_write,
                        }
                        for intent in decision.intents
                    ],
                    "clarification_present": bool(decision.clarification),
                    "model_status": model.last_status,
                    "schema_errors": model.last_schema_errors,
                    "usage": model.last_usage,
                }
            )
        except Exception:  # noqa: BLE001 - runtime exceptions may contain model text or credentials
            rows.append(
                {
                    "case_id": case_id,
                    "task_status": "UNKNOWN",
                    "component_status": "ERROR",
                    "reason": "ROUTER_ERROR",
                }
            )
    app_root = Path(__file__).resolve().parents[2]
    source_paths = (
        "agent/routing.py",
        "evaluation/task_eval/routing_model_probe.py",
        "evaluation/task_eval/routing_intent_cases.py",
    )
    return {
        "scope": "synthetic_dev_hybrid_routing_only",
        "dataset_sha256": dataset.sha256,
        "created_at": datetime.now(UTC).isoformat(),
        "router_prompt_sha256": SYSTEM_SHA256,
        "source_sha256": {
            path: hashlib.sha256((app_root / path).read_bytes()).hexdigest()
            for path in source_paths
        },
        "model": MODEL,
        "task_status": "UNKNOWN",
        "model_calls": model.attempts,
        "local_budget": budget.snapshot(),
        "cases": rows,
        "limitations": [
            "No business service, authorization, final answer, provider-side spending cap or human review was verified.",
            "The provider model alias is not an immutable model snapshot; comparisons are exploratory.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--budget-file", required=True)
    parser.add_argument("--budget-limit-nano", required=True, type=int)
    parser.add_argument("--budget-opening-nano", required=True, type=int)
    parser.add_argument("--minimum-reserved-nano", required=True, type=int)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--confirm-current-pricing", action="store_true", required=True)
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args()
    output = None
    try:
        key_path = Path(args.credential_file)
        if (
            key_path.is_symlink()
            or not key_path.is_file()
            or stat.S_IMODE(key_path.stat().st_mode) & 0o077
            or args.minimum_reserved_nano < 0
        ):
            raise ValueError("credential or budget history unavailable")
        budget = DurableBudget.open_existing(
            args.budget_file,
            limit_nano=args.budget_limit_nano,
            opening_nano=args.budget_opening_nano,
        )
        if budget.snapshot()["reserved_nano"] < args.minimum_reserved_nano:
            raise ValueError("budget history unavailable")
        dataset = load_dataset(args.dataset)
        # Reserve the report path before reading the credential or attempting a call.
        output = Path(args.report_file).open("x", encoding="utf-8")  # noqa: SIM115
        result = asyncio.run(
            evaluate_dev_routing_model(
                dataset,
                credential=key_path.read_text(encoding="utf-8").strip(),
                budget=budget,
                max_calls=args.max_calls,
                execute=args.execute,
                confirmed_current_pricing=args.confirm_current_pricing,
            )
        )
        output.write(json.dumps(result, ensure_ascii=False) + "\n")
        output.close()
        print(json.dumps({"status": "INCOMPLETE", "model_calls": result["model_calls"]}))
        return 3
    except Exception:  # noqa: BLE001 - do not disclose credentials, inputs or provider output
        if output is not None:
            output.close()
        print(json.dumps({"status": "ERROR", "reason": "ROUTING_PROBE_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
