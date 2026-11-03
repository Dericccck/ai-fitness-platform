"""Permanent UNKNOWN records back off without starving later durable outcomes."""

import asyncio
from types import SimpleNamespace

import pytest

from app.confirmation.reconciliation_worker import ConfirmationReconciliationWorker


class ScheduledRepository:
    def __init__(self, unknown_count=3):
        self.rows = [
            SimpleNamespace(
                id=f"record-{index}",
                execution_status="UNKNOWN",
                due=0,
                reconciliation_claim_token=None,
                reconciliation_attempts=0,
            )
            for index in range(unknown_count + 1)
        ]
        self.completed = []
        self.sequence = 0

    async def claim_reconciliation_batch(self, *, limit, **kwargs):
        rows = [
            row
            for row in self.rows
            if row.execution_status == "UNKNOWN"
            and row.due == 0
            and row.reconciliation_claim_token is None
        ][:limit]
        for row in rows:
            self.sequence += 1
            row.reconciliation_claim_token = f"claim-{self.sequence}"
            row.reconciliation_attempts += 1
        return rows

    async def finish_reconciliation_attempt(self, record_id, token, **kwargs):
        row = next(row for row in self.rows if row.id == record_id)
        assert row.reconciliation_claim_token == token
        row.reconciliation_claim_token = None
        row.due = kwargs["retry_base_seconds"]
        self.completed.append(record_id)
        return True


async def test_more_than_batch_permanent_unknown_does_not_starve_later_success():
    repository = ScheduledRepository(unknown_count=3)
    calls = []

    class Reconciler:
        async def reconcile_stored_execution(self, record, **kwargs):
            calls.append(record.id)
            if record.id == "record-3":
                record.execution_status = "SUCCEEDED"
            return record

    worker = ConfirmationReconciliationWorker(
        repository, reconciler=Reconciler(), batch_size=2, concurrency=1
    )
    first = await worker.run_once()
    second = await worker.run_once()

    assert first.scanned == 2 and first.reconciled == 0
    assert second.scanned == 2 and second.reconciled == 1
    assert calls == ["record-0", "record-1", "record-2", "record-3"]
    assert [row.execution_status for row in repository.rows[:3]] == ["UNKNOWN"] * 3
    assert (await worker.run_once()).scanned == 0
    assert all(row.reconciliation_attempts == 1 for row in repository.rows)


async def test_query_timeout_releases_only_read_lease_and_schedules_backoff():
    repository = ScheduledRepository(unknown_count=0)

    class SlowReconciler:
        async def reconcile_stored_execution(self, record, **kwargs):
            await asyncio.sleep(10)

    result = await ConfirmationReconciliationWorker(
        repository,
        reconciler=SlowReconciler(),
        query_timeout_seconds=0.01,
        lease_seconds=6,
        batch_size=1,
    ).run_once()

    assert result.query_failures == 1
    assert repository.rows[0].execution_status == "UNKNOWN"
    assert repository.rows[0].reconciliation_claim_token is None
    assert repository.rows[0].due == 30


async def test_query_error_logs_only_type_and_never_exception_parameters(monkeypatch):
    repository = ScheduledRepository(unknown_count=0)
    messages = []

    class BrokenReconciler:
        async def reconcile_stored_execution(self, record, **kwargs):
            raise RuntimeError("password=private-credential; actor=private-user")

    monkeypatch.setattr(
        "app.confirmation.reconciliation_worker._logger.warning",
        lambda event, **fields: messages.append((event, fields)),
    )
    result = await ConfirmationReconciliationWorker(
        repository, reconciler=BrokenReconciler()
    ).run_once()

    assert result.query_failures == 1
    assert messages == [("confirmation_reconciliation_failed", {"error_type": "RuntimeError"})]
    assert "private" not in repr(messages)


@pytest.mark.parametrize(
    "options",
    [
        {"batch_size": 0},
        {"concurrency": 0},
        {"query_timeout_seconds": 61, "lease_seconds": 60},
        {"retry_base_seconds": 60, "retry_max_seconds": 30},
    ],
)
def test_invalid_scheduling_bounds_are_rejected(options):
    with pytest.raises(ValueError):
        ConfirmationReconciliationWorker(ScheduledRepository(), **options)
