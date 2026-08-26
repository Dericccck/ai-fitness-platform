"""主动提醒 Inbox、RabbitMQ ACK 和 Worker 重试的故障恢复回归。

这些测试不连接真实 RabbitMQ 或 PostgreSQL，而是模拟消息和事务边界，专门验证最容易
造成丢消息或重复通知的时序：消息重复投递、Inbox 事务失败、Worker 处理失败后重试。
真实环境仍需使用专用测试数据执行跨服务 live-check，不能用本文件替代生产演练。
"""

from datetime import UTC, datetime
from typing import Any, Self

import pytest

from app.proactive.events import ProactiveEventMessage
from app.proactive.rabbit_consumer import ProactiveRabbitConsumer
from app.proactive.repository import ProactiveEventRecord
from app.proactive.worker import ProactiveEventWorker


def _event() -> ProactiveEventMessage:
    return ProactiveEventMessage.model_validate(
        {
            "event_id": "appointment-created:appointment-1",
            "source": "booking",
            "event_type": "APPOINTMENT_CREATED",
            "aggregate_id": "appointment-1",
            "organization_id": "org-1",
            "payload": {"studentId": "student-1", "coachId": "coach-1"},
        }
    )


class _FakeMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.ack_count = 0
        self.reject_requeue_values: list[bool] = []

    async def ack(self) -> None:
        self.ack_count += 1

    async def reject(self, *, requeue: bool) -> None:
        self.reject_requeue_values.append(requeue)


class _FakeTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.begin_count = 0

    def begin(self) -> _FakeTransaction:
        self.begin_count += 1
        return _FakeTransaction()


class _FakeDatabase:
    def __init__(self) -> None:
        self.engine = _FakeEngine()


class _FakeConsumerRepository:
    def __init__(self, *, accepted: bool = True, error: Exception | None = None) -> None:
        self.accepted = accepted
        self.error = error
        self.events: list[ProactiveEventMessage] = []

    async def accept(self, connection: Any, *, event: ProactiveEventMessage) -> bool:
        if self.error is not None:
            raise self.error
        self.events.append(event)
        return self.accepted


def _consumer(repository: _FakeConsumerRepository) -> ProactiveRabbitConsumer:
    return ProactiveRabbitConsumer(
        _FakeDatabase(),
        repository,
        url="amqp://test",
        exchange_name="fitness.domain.events",
        queue_name="fitness.proactive.events",
        routing_key="appointment.created",
    )


@pytest.mark.asyncio
async def test_duplicate_event_is_acked_without_second_inbox_insert() -> None:
    """RabbitMQ 重投同一个 event_id 时必须 ACK，但不能再次写入 Inbox。"""

    repository = _FakeConsumerRepository(accepted=False)
    message = _FakeMessage(_event().model_dump_json().encode("utf-8"))

    await _consumer(repository)._consume_one(message)

    assert message.ack_count == 1
    assert message.reject_requeue_values == []
    assert [event.event_id for event in repository.events] == ["appointment-created:appointment-1"]


@pytest.mark.asyncio
async def test_invalid_event_is_rejected_to_dead_letter_without_ack() -> None:
    """契约非法消息应进入死信路径，不能 ACK 后静默丢弃。"""

    repository = _FakeConsumerRepository()
    message = _FakeMessage(b'{"eventId":"event-1","eventType":"UNSUPPORTED"}')

    await _consumer(repository)._consume_one(message)

    assert message.ack_count == 0
    assert message.reject_requeue_values == [False]
    assert repository.events == []


@pytest.mark.asyncio
async def test_inbox_transaction_failure_does_not_ack_message() -> None:
    """Inbox 事务失败时不 ACK，让 RabbitMQ 重新投递而不是造成事件丢失。"""

    repository = _FakeConsumerRepository(error=RuntimeError("database unavailable"))
    message = _FakeMessage(_event().model_dump_json().encode("utf-8"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        await _consumer(repository)._consume_one(message)

    assert message.ack_count == 0
    assert message.reject_requeue_values == []


class _FakeProactiveRepository:
    def __init__(self, record: ProactiveEventRecord) -> None:
        self.record = record
        self.claim_count = 0
        self.retry_events: list[str] = []
        self.processed_events: list[str] = []

    async def claim_batch(
        self, connection: Any, *, worker_id: str, limit: int
    ) -> list[ProactiveEventRecord]:
        self.claim_count += 1
        return [self.record] if self.claim_count <= 2 else []

    async def mark_retry(
        self,
        connection: Any,
        *,
        event_id: str,
        worker_id: str,
        error_code: str,
        max_attempts: int,
    ) -> bool:
        self.retry_events.append(f"{event_id}:{error_code}")
        return True

    async def mark_processed(self, connection: Any, *, event_id: str, worker_id: str) -> bool:
        self.processed_events.append(event_id)
        return True


class _FakeNotificationRepository:
    def __init__(self) -> None:
        self.enqueued_targets: list[str] = []

    async def enqueue_on_connection(
        self,
        connection: Any,
        *,
        notification_type: str,
        subject_user_id: str,
        organization_id: str,
        aggregate_type: str,
        aggregate_id: str,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> None:
        self.enqueued_targets.append(subject_user_id)


def _record() -> ProactiveEventRecord:
    now = datetime.now(UTC)
    return ProactiveEventRecord(
        event_id="appointment-created:appointment-1",
        source="booking",
        event_type="APPOINTMENT_CREATED",
        aggregate_id="appointment-1",
        organization_id="org-1",
        payload={"studentId": "student-1", "coachId": "coach-1"},
        status="PROCESSING",
        attempt_count=0,
        available_at=now,
        locked_by="worker-1",
        locked_at=now,
    )


@pytest.mark.asyncio
async def test_worker_failure_is_retryable_and_restart_can_finish_event() -> None:
    """第一次处理失败进入重试，第二个 Worker 领取后完成事件和两条通知。"""

    database = _FakeDatabase()
    event_repository = _FakeProactiveRepository(_record())
    notification_repository = _FakeNotificationRepository()
    worker = ProactiveEventWorker(
        database,
        event_repository,
        notification_repository,
        worker_id="worker-1",
        max_attempts=3,
    )
    original_process_one = worker._process_one
    process_calls = 0

    async def fail_once(record: ProactiveEventRecord) -> None:
        nonlocal process_calls
        process_calls += 1
        if process_calls == 1:
            raise RuntimeError("notification database temporarily unavailable")
        await original_process_one(record)

    worker._process_one = fail_once

    first = await worker.run_once()
    second = await worker.run_once()

    assert first.claimed == 1
    assert first.processed == 0
    assert first.retried == 1
    assert second.claimed == 1
    assert second.processed == 1
    assert second.retried == 0
    assert event_repository.retry_events == [
        "appointment-created:appointment-1:PROACTIVE_EVENT_PROCESSING_FAILED"
    ]
    assert event_repository.processed_events == ["appointment-created:appointment-1"]
    assert notification_repository.enqueued_targets == ["student-1", "coach-1"]
