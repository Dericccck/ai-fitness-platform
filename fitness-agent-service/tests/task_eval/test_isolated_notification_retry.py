"""Fault-injected real in-app worker against an explicitly disposable local PG.

Only a synthetic identity and local in-app adapter are used. A process-local
socket restriction is included; it is not a host/container egress firewall.
"""

from __future__ import annotations

import os
import socket
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.infrastructure.database import Database
from app.notifications.channels import InAppNotificationChannelAdapter
from app.notifications.outbox import NotificationOutboxRepository
from app.notifications.worker import NotificationOutboxWorker

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1"
    or os.getenv("TASK_EVAL_RESTRICT_NOTIFICATION_NETWORK") != "1",
    reason="requires explicitly disposable isolated PostgreSQL launcher",
)


class _FailAfterInboxWrite(InAppNotificationChannelAdapter):
    async def deliver(self, connection: object, request: Any) -> Any:
        await super().deliver(connection, request)
        raise RuntimeError("synthetic injected failure after inbox write")


async def test_notification_inbox_rollback_retry_once(monkeypatch: Any) -> None:
    settings = Settings(_env_file=None)
    url = make_url(settings.database_url)
    assert url.host == "127.0.0.1" and url.port and url.username == "fitness_eval"
    assert isinstance(url.database, str) and url.database.startswith("fitness_runtime_acceptance_")
    original_connect = socket.socket.connect
    blocked: list[object] = []

    def allow_database_only(sock: socket.socket, address: Any) -> Any:
        if address != ("127.0.0.1", url.port):
            blocked.append(address)
            raise PermissionError("non-test notification destination blocked")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", allow_database_only)
    with socket.socket() as probe, pytest.raises(PermissionError):
        probe.connect(("192.0.2.1", 443))
    assert len(blocked) == 1
    blocked.clear()
    database = Database(settings)
    repository = NotificationOutboxRepository()
    subject = f"synthetic-notification-{uuid4()}"
    key = f"synthetic-fault:{uuid4()}"
    try:
        async with database.engine.begin() as connection:
            assert await repository.enqueue_on_connection(
                connection,
                notification_type="MEMORY_CANDIDATE_PENDING",
                subject_user_id=subject,
                organization_id="synthetic-eval-org",
                aggregate_type="memory_candidate",
                aggregate_id=f"synthetic-candidate-{uuid4()}",
                dedupe_key=key,
                payload={},
            )
            assert not await repository.enqueue_on_connection(
                connection,
                notification_type="MEMORY_CANDIDATE_PENDING",
                subject_user_id=subject,
                organization_id="synthetic-eval-org",
                aggregate_type="memory_candidate",
                aggregate_id="different-duplicate",
                dedupe_key=key,
                payload={},
            )
        failure_worker = NotificationOutboxWorker(
            database,
            repository,
            worker_id="synthetic-failing-worker",
            channel_adapters={"IN_APP": _FailAfterInboxWrite(repository)},
        )
        first = await failure_worker.run_once()
        assert (first.claimed, first.retried, first.published) == (1, 1, 0)
        async with database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, status, attempt_count FROM agent_notification_outbox WHERE dedupe_key=:key"
                        ),
                        {"key": key},
                    )
                )
                .mappings()
                .one()
            )
            outbox_id = row["id"]
            assert (row["status"], row["attempt_count"]) == ("RETRYABLE_FAILED", 1)
            count = await connection.scalar(
                text("SELECT count(*) FROM agent_in_app_notifications WHERE dedupe_key=:key"),
                {"key": key},
            )
            assert count == 0
            attempts = (
                await connection.execute(
                    text(
                        "SELECT attempt_no, status FROM agent_notification_delivery_attempts "
                        "WHERE outbox_id=:id ORDER BY attempt_no"
                    ),
                    {"id": outbox_id},
                )
            ).all()
            assert attempts == [(1, "RETRYABLE_FAILED")]
            # Deterministically advance only this test row's retry eligibility.
            await connection.execute(
                text(
                    "UPDATE agent_notification_outbox SET available_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": outbox_id},
            )
        success_worker = NotificationOutboxWorker(
            database, repository, worker_id="synthetic-success-worker"
        )
        second = await success_worker.run_once()
        assert (second.claimed, second.published, second.retried) == (1, 1, 0)
        third = await success_worker.run_once()
        assert (third.claimed, third.published) == (0, 0)
        async with database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status, attempt_count FROM agent_notification_outbox WHERE id=:id"
                        ),
                        {"id": outbox_id},
                    )
                )
                .mappings()
                .one()
            )
            assert (row["status"], row["attempt_count"]) == ("PUBLISHED", 1)
            inbox = (
                await connection.execute(
                    text(
                        "SELECT subject_user_id, organization_id FROM agent_in_app_notifications WHERE dedupe_key=:key"
                    ),
                    {"key": key},
                )
            ).all()
            assert inbox == [(subject, "synthetic-eval-org")]
            attempts = (
                await connection.execute(
                    text(
                        "SELECT attempt_no, status FROM agent_notification_delivery_attempts "
                        "WHERE outbox_id=:id ORDER BY attempt_no"
                    ),
                    {"id": outbox_id},
                )
            ).all()
            assert attempts == [(1, "RETRYABLE_FAILED"), (2, "SUCCEEDED")]
        assert not blocked
    finally:
        await database.close()
