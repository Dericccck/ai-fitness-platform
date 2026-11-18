"""Real PostgreSQL inbox test for duplicate synthetic booking events.

This tests event-ingress idempotency only, not the booking write or RabbitMQ.
The random, exclusively local acceptance database must be created by the
isolated runtime launcher and is deleted by that launcher afterwards.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.infrastructure.database import Database
from app.proactive.events import ProactiveEventMessage
from app.proactive.repository import ProactiveEventRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RUNTIME_DELIVERY_TESTS") != "1",
    reason="requires isolated runtime launcher",
)


async def test_duplicate_booking_event_is_inserted_once_concurrently() -> None:
    settings = Settings(_env_file=None)
    url = make_url(settings.database_url)
    assert url.host == "127.0.0.1" and (url.database or "").startswith(
        "fitness_runtime_acceptance_"
    )
    database = Database(settings)
    event = ProactiveEventMessage.model_validate(
        {
            "eventId": "booking-event-" + uuid4().hex,
            "source": "booking",
            "eventType": "APPOINTMENT_CREATED",
            "aggregateId": "fixture-booking-1",
            "organizationId": "fixture-org-1",
            "payload": {"studentId": "fixture-student-1", "coachId": "fixture-coach-1"},
        }
    )
    repo = ProactiveEventRepository()

    async def accept() -> bool:
        async with database.engine.begin() as connection:
            return await repo.accept(connection, event=event)

    try:
        accepted = await asyncio.gather(*(accept() for _ in range(8)))
        assert accepted.count(True) == 1
        assert accepted.count(False) == 7
        async with database.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT COUNT(*) FROM agent_proactive_event_inbox WHERE event_id = :event_id"),
                {"event_id": event.event_id},
            )
            assert result.scalar_one() == 1
    finally:
        await database.close()
