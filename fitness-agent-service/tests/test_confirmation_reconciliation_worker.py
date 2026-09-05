from datetime import UTC, datetime

import pytest

from app.confirmation.reconciliation_worker import ConfirmationReconciliationWorker


class _Record:
    id = "c-1"


class _Repository:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str]] = []

    async def list_stale_running(self, *, older_than_seconds: int, limit: int):
        assert older_than_seconds == 120
        assert limit == 2
        return [_Record()]

    async def mark_unknown(self, confirmation_id, now, trace_id):
        assert confirmation_id == "c-1"
        assert now.tzinfo == UTC
        self.marked.append((confirmation_id, trace_id))


@pytest.mark.asyncio
async def test_reconciliation_marks_stale_running_as_unknown() -> None:
    repository = _Repository()
    worker = ConfirmationReconciliationWorker(repository, older_than_seconds=120, batch_size=2)

    result = await worker.run_once()

    assert result.scanned == 1
    assert result.marked_unknown == 1
    assert repository.marked == [("c-1", "reconciliation-worker")]


@pytest.mark.asyncio
async def test_reconciliation_continues_when_one_record_fails() -> None:
    class FailingRepository(_Repository):
        async def list_stale_running(self, *, older_than_seconds: int, limit: int):
            return [_Record(), type("Record", (), {"id": "c-2"})()]

        async def mark_unknown(self, confirmation_id, now: datetime, trace_id):
            if confirmation_id == "c-1":
                raise RuntimeError("transient")
            self.marked.append((confirmation_id, trace_id))

    repository = FailingRepository()
    result = await ConfirmationReconciliationWorker(repository).run_once()

    assert result.scanned == 2
    assert result.marked_unknown == 1
    assert repository.marked == [("c-2", "reconciliation-worker")]


@pytest.mark.asyncio
async def test_unknown_booking_is_automatically_queried_until_success() -> None:
    record = type(
        "Record",
        (),
        {"id": "c-1", "execution_status": "UNKNOWN", "tool_id": "fitness.booking.create.v1"},
    )()

    class UnknownRepository(_Repository):
        async def list_stale_running(self, *, older_than_seconds: int, limit: int):
            return [record]

    class Reconciler:
        async def reconcile_stored_execution(self, current, *, trace_id: str):
            assert current is record
            assert trace_id == "reconciliation-worker"
            return type("Result", (), {"execution_status": "SUCCEEDED"})()

    result = await ConfirmationReconciliationWorker(
        UnknownRepository(), reconciler=Reconciler()
    ).run_once()

    assert result.marked_unknown == 0
    assert result.reconciled == 1
