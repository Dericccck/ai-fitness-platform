import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import ToolConfirmationNormalizationError
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService, _validate_future_booking_start
from app.evaluation.task_eval import booking_local as local
from app.infrastructure.gateway_client import GatewayRequestContext


def payload(start: datetime) -> bytes:
    return json.dumps({"startTime": start.isoformat()}).encode()


def test_booking_confirmation_requires_a_future_instant():
    now = datetime(2026, 11, 21, tzinfo=UTC)
    for tool in ("fitness.booking.create.v1", "fitness.booking.reschedule.v1"):
        _validate_future_booking_start(tool, payload(now + timedelta(seconds=1)), now)
        for value in (now, now - timedelta(seconds=1)):
            with pytest.raises(ToolConfirmationNormalizationError):
                _validate_future_booking_start(tool, payload(value), now)
        with pytest.raises(ToolConfirmationNormalizationError):
            _validate_future_booking_start(tool, b'{"startTime":"2030-01-01T00:00:00"}', now)
    _validate_future_booking_start("fitness.booking.cancel.v1", b"{}", now)


@pytest.mark.asyncio
async def test_past_booking_does_not_create_confirmation():
    repository = local.Repository()
    gateway = local.Gateway(repository, "approve")
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="test"),
        cast(Any, local.TokenIssuer()),
    )
    now = datetime.now(UTC)
    data = {
        **local.PAYLOAD,
        "start_time": (now - timedelta(hours=2)).isoformat(),
        "end_time": (now - timedelta(hours=1)).isoformat(),
    }
    try:
        with pytest.raises(ToolConfirmationNormalizationError):
            await service.prepare(
                tool_id="fitness.booking.create.v1",
                raw_input=data,
                gateway_context=GatewayRequestContext(signed_context="signed-context"),
                identity=local.IDENTITY,
                thread_id="test-thread",
            )
        assert not hasattr(repository, "current")
        assert gateway.attempts == 0
    finally:
        gateway.db.close()
