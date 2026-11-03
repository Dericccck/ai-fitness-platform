"""Confirmation-compatible deterministic encoding; array order is business data."""

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class CanonicalEncodingError(ValueError):
    pass


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalEncodingError("Non-finite number")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _canonicalize(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalEncodingError("Value is not canonical JSON") from exc


def training_execution_payload(day_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"dayId": day_id, "status": payload["status"], "note": payload.get("note")}


def gateway_confirmation_body(tool_id: str, payload: dict[str, Any]) -> bytes:
    if tool_id == "fitness.training.day.record_execution.v1":
        payload = training_execution_payload(payload["day_id"], payload)
    return canonical_json_bytes(payload)
