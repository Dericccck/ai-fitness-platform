"""Opt-in, single-call synthetic model connectivity smoke test.

No business service, tool, database, production credentials, or Agent runtime
is invoked. This is not an Agent task evaluation or proof of sandbox isolation.
"""

from __future__ import annotations

import json
from decimal import ROUND_CEILING, Decimal
from typing import Any, TypeGuard
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .budget_store import DurableBudget
from .model_safety import BudgetLedger, ConnectivityBudget, ModelPreflightConfig

# The entire message is frozen in source, and matched against the selected dev
# case before sending. Arbitrary datasets cannot turn this into an exfiltration
# channel by merely claiming that their contents are synthetic.
CASE_ID = "ROUT-001"
PROMPT = "帮我预约明天下午的私教课"
_ALLOWED_PROVIDERS = frozenset({"api.deepseek.com", "api.openai.com"})
_MAX_INPUT_TOKENS = 512  # conservative declared bound; provider usage is checked
_SMOKE_MAX_OUTPUT_TOKENS = 64
_CONNECTIVITY_MAX_OUTPUT_TOKENS = 256
_MAX_RESPONSE_BYTES = 65536


def _count(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validated_usage(usage: Any, *, deepseek: bool) -> tuple[int | None, int | None]:
    """Reject inconsistent accounting; never infer an unknown count as zero."""
    if not isinstance(usage, dict):
        return None, None
    prompt, completion, total = (
        usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    if not _count(prompt) or not _count(completion) or not _count(total):
        return None, None
    if prompt + completion != total:
        return None, None
    if deepseek:
        hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
        if not _count(hit) or not _count(miss) or hit + miss != prompt:
            return None, None
        details = usage.get("prompt_tokens_details")
        if details is not None and (
            not isinstance(details, dict)
            or not _count(details.get("cached_tokens"))
            or details.get("cached_tokens") != hit
        ):
            return None, None
    # We declared no provider-side tools; any reported use could be billed
    # outside the token budget and must not be presented as a known cost.
    if usage.get("server_tool_use") not in (None, {}):
        return None, None
    details = usage.get("completion_tokens_details")
    if details is not None:
        if not isinstance(details, dict):
            return None, None
        for name in ("reasoning_tokens", "safety_tokens"):
            if name in details and (not _count(details[name]) or details[name] > completion):
                return None, None
    return prompt, completion


def smoke_once(
    dataset: Any,
    config: ModelPreflightConfig,
    *,
    credential: str,
    execute: bool = False,
    confirmed_external_controls: bool = False,
    transport: httpx.MockTransport | None = None,
    durable_budget: DurableBudget | None = None,
) -> dict[str, Any]:
    """Attempt at most one model call and return a non-sensitive observation.

    Real requests also require a persistent conservative reservation. Declared
    rates remain estimates; a provider-side hard cap and independently verified
    environment remain the operator's responsibility.
    """
    if not execute or not confirmed_external_controls:
        raise ValueError("explicit execution and external controls confirmation are required")
    if any(
        value.lower().startswith("replace")
        for value in (
            config.environment_id,
            config.isolation_evidence_ref,
            config.authorization_ref,
            config.provider_spend_limit_ref,
            config.pricing_version,
        )
    ):
        raise ValueError(
            "placeholder environment, authorization, limit or pricing references are not permitted"
        )
    if not isinstance(credential, str) or not credential.strip():
        raise ValueError("dedicated evaluation credential is required")
    if any(
        credential in value
        for value in (config.model, config.pricing_version, dataset.dataset_version)
    ):
        raise ValueError("credential cannot be used as report metadata")
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise ValueError("custom network transports are not permitted")
    if durable_budget is not None and not isinstance(durable_budget, DurableBudget):
        raise TypeError("persistent evaluation budget has an invalid type")
    if transport is None and durable_budget is None:
        raise ValueError("real requests require a persistent evaluation budget")
    if durable_budget is not None and config.currency != "CNY":
        raise ValueError("persistent budget requires CNY pricing")
    endpoint = urlsplit(config.endpoint)
    if endpoint.hostname not in _ALLOWED_PROVIDERS:
        raise ValueError("model smoke provider is not on the official provider allowlist")
    case = _validate_case(dataset)
    return _perform_call(
        dataset,
        case,
        config,
        credential,
        endpoint.hostname,
        transport,
        connectivity=False,
        durable_budget=durable_budget,
    )


def _validate_case(dataset: Any) -> Any:
    matches = [c for c in dataset.cases if c.case_id == CASE_ID]
    if len(matches) != 1:
        raise ValueError("frozen synthetic development case missing")
    case = matches[0]
    messages = [s.message for s in case.steps if s.type == "chat"]
    if case.split != "dev" or case.source.type != "synthetic" or messages != [PROMPT]:
        raise ValueError("frozen synthetic development case does not match")

    return case


def connectivity_once(
    dataset: Any,
    *,
    credential: str,
    confirmed_single_call: bool = False,
    confirmed_current_pricing: bool = False,
    transport: httpx.MockTransport | None = None,
    durable_budget: DurableBudget | None = None,
) -> dict[str, Any]:
    """One budgeted model connectivity request; does not attest sandbox isolation."""
    if not confirmed_single_call or not confirmed_current_pricing:
        raise ValueError("explicit single-call and current-pricing confirmations are required")
    if not isinstance(credential, str) or not credential.strip():
        raise ValueError("dedicated evaluation credential is required")
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise ValueError("custom network transports are not permitted")
    if durable_budget is not None and not isinstance(durable_budget, DurableBudget):
        raise TypeError("persistent evaluation budget has an invalid type")
    if transport is None and durable_budget is None:
        raise ValueError("real requests require a persistent evaluation budget")
    if credential in dataset.dataset_version:
        raise ValueError("credential cannot be used as report metadata")
    case = _validate_case(dataset)
    # Snapshot from official CNY peak cache-miss input/output rates, multiplied
    # by 10. This is not a provider hard cap; require current-price review.
    config = ConnectivityBudget(max_cost="0.1")
    return _perform_call(
        dataset,
        case,
        config,
        credential,
        "api.deepseek.com",
        transport,
        connectivity=True,
        durable_budget=durable_budget,
    )


def _perform_call(
    dataset: Any,
    case: Any,
    config: ModelPreflightConfig | ConnectivityBudget,
    credential: str,
    provider_host: str,
    transport: httpx.MockTransport | None,
    *,
    connectivity: bool,
    durable_budget: DurableBudget | None,
) -> dict[str, Any]:
    ledger = BudgetLedger(config)
    output_limit = _CONNECTIVITY_MAX_OUTPUT_TOKENS if connectivity else _SMOKE_MAX_OUTPUT_TOKENS
    reservation = ledger.reserve(
        input_tokens_upper_bound=_MAX_INPUT_TOKENS, output_tokens_upper_bound=output_limit
    )
    request_id = uuid4().hex
    if durable_budget is not None:
        reserve_nano = int(
            (reservation.cost * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING)
        )
        durable_budget.reserve(request_id, reserve_nano)
    usage_in: int | None = None
    usage_out: int | None = None
    result = {
        "scope": "single_model_call_only",
        "status": "INCOMPLETE",
        "reason": "MODEL_CALL_UNCERTAIN",
        "case_id": CASE_ID,
        "dataset_sha256": dataset.sha256,
        "dataset_version": dataset.dataset_version,
        "review_status": case.review.status,
        "model": config.model,
        "provider_host": provider_host,
        "attempts": 1,
        "reserved_cost": str(reservation.cost),
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost": None,
        "currency": config.currency,
        "pricing_version": config.pricing_version,
        "output_present": None,
        "finish_reason": None,
        "actual_cost": None,
        "cost_basis": "Declared rates applied to all prompt and completion tokens; actual provider charge is unknown.",
        "provider_spend_cap": "NOT_VERIFIED"
        if connectivity
        else "externally_declared_not_verified",
        "environment_isolation": "NOT_VERIFIED",
        "pricing_verification": "operator_confirmed_not_runtime_verified"
        if connectivity
        else "externally_declared_not_verified",
        "notes": [
            "One synthetic model call; not an Agent task result or production-quality score.",
            "Local reservations are not a provider billing cap; current pricing and charges can change.",
            "Cost is an estimate at declared rates; external provider hard cap and environment isolation must be independently verified.",
            "For conservative reservation, verify current maximum peak/cache-miss input and output rates before running; prices may change.",
        ],
    }
    try:
        url = f"https://{provider_host}/v1/chat/completions"
        request_body = {
            "model": config.model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": output_limit,
            "temperature": 0,
            "stream": False,
        }
        if provider_host == "api.deepseek.com":
            # DeepSeek thinking is on by default; explicitly disable it so a
            # small output cap is not consumed by reasoning content.
            request_body["thinking"] = {"type": "disabled"}
        with (
            httpx.Client(
                timeout=20.0, trust_env=False, follow_redirects=False, transport=transport
            ) as client,
            client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {credential}"},
                json=request_body,
            ) as response,
        ):
            if response.status_code != 200:
                result["reason"] = "PROVIDER_HTTP_ERROR"
            else:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("response too large")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("invalid response")
                usage = payload.get("usage")
                usage_in, usage_out = _validated_usage(
                    usage, deepseek=provider_host == "api.deepseek.com"
                )
                if provider_host == "api.deepseek.com" and isinstance(usage, dict):
                    completion_details = usage.get("completion_tokens_details")
                    if isinstance(completion_details, dict) and completion_details.get(
                        "reasoning_tokens"
                    ):
                        result["reason"] = "UNEXPECTED_REASONING_OR_TOOL_CALL"
                choices = payload.get("choices")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                    choice = choices[0]
                    message = choice.get("message")
                    reason = choice.get("finish_reason")
                    known_reasons = {
                        "stop",
                        "length",
                        "content_filter",
                        "tool_calls",
                        "insufficient_system_resource",
                        "aborted",
                    }
                    result["finish_reason"] = (
                        reason if isinstance(reason, str) and reason in known_reasons else "unknown"
                    )
                    if isinstance(message, dict):
                        content = message.get("content")
                        result["output_present"] = isinstance(content, str) and bool(
                            content.strip()
                        )
                        if (
                            message.get("reasoning_content")
                            or message.get("tool_calls")
                            or message.get("server_tool_calls")
                        ):
                            result["reason"] = "UNEXPECTED_REASONING_OR_TOOL_CALL"
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        result["reason"] = "MODEL_RESPONSE_OR_TRANSPORT_ERROR"
    finally:
        if result["reason"] == "MODEL_CALL_UNCERTAIN":
            if result["finish_reason"] == "length":
                result["reason"] = "MODEL_OUTPUT_TRUNCATED"
            elif result["finish_reason"] not in {None, "stop"}:
                result["reason"] = "MODEL_DID_NOT_FINISH_NORMALLY"
        verified = (
            result["reason"] == "MODEL_CALL_UNCERTAIN"
            and result["finish_reason"] == "stop"
            and result["output_present"] is True
            and isinstance(usage_in, int)
            and not isinstance(usage_in, bool)
            and isinstance(usage_out, int)
            and not isinstance(usage_out, bool)
            and 0 < usage_in <= _MAX_INPUT_TOKENS
            and 0 < usage_out <= output_limit
        )
        ledger.settle(
            reservation,
            input_tokens=usage_in if verified else None,
            output_tokens=usage_out if verified else None,
        )
        if durable_budget is not None:
            durable_budget.finish(request_id, usage_known=verified and not ledger.stopped)

    if ledger.stopped:
        usage_valid = (
            isinstance(usage_in, int)
            and not isinstance(usage_in, bool)
            and isinstance(usage_out, int)
            and not isinstance(usage_out, bool)
            and 0 < usage_in <= _MAX_INPUT_TOKENS
            and 0 < usage_out <= output_limit
        )
        if not usage_valid or result["reason"] not in {
            "MODEL_OUTPUT_TRUNCATED",
            "MODEL_DID_NOT_FINISH_NORMALLY",
            "UNEXPECTED_REASONING_OR_TOOL_CALL",
        }:
            result["reason"] = "USAGE_UNKNOWN_OR_EXCEEDS_RESERVATION"
    else:
        result.update(
            input_tokens=usage_in,
            output_tokens=usage_out,
            estimated_cost=str(ledger.cost_committed),
        )
        if result["reason"] == "UNEXPECTED_REASONING_OR_TOOL_CALL":
            pass
        elif result["finish_reason"] == "length":
            result["reason"] = "MODEL_OUTPUT_TRUNCATED"
        elif result["finish_reason"] != "stop":
            result["reason"] = "MODEL_DID_NOT_FINISH_NORMALLY"
        elif result["output_present"]:
            result.update(status="OBSERVED", reason="MODEL_RESPONSE_RECEIVED")
        else:
            result["reason"] = "MODEL_OUTPUT_MISSING"
    if durable_budget is not None:
        result["local_budget"] = durable_budget.snapshot()
    return result
