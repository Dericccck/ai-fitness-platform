import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.core.metrics import HttpMetrics
from app.memory.candidate_worker import MemoryCandidateExpiryWorker


class FakeCandidateService:
    def __init__(self, expired: int) -> None:
        self.expired = expired
        self.limits: list[int] = []

    async def expire_due(self, *, limit: int) -> int:
        self.limits.append(limit)
        return self.expired


@pytest.mark.asyncio
async def test_candidate_expiry_worker_runs_bounded_batch_and_records_metrics() -> None:
    metrics = HttpMetrics.create(
        service_name="fitness-agent-service",
        service_version="test",
        environment="test",
        registry=CollectorRegistry(),
    )
    service = FakeCandidateService(expired=3)
    worker = MemoryCandidateExpiryWorker(service, batch_size=20, metrics=metrics)  # type: ignore[arg-type]

    result = await worker.run_once()

    assert result.expired == 3
    assert service.limits == [20]
    exposition = generate_latest(metrics.registry).decode()
    assert 'worker="memory_candidate_expiry"' in exposition
    assert 'outcome="expired"' in exposition


def test_candidate_expiry_worker_rejects_unsafe_batch_size() -> None:
    with pytest.raises(ValueError, match="between 1 and 5000"):
        MemoryCandidateExpiryWorker(FakeCandidateService(expired=0), batch_size=0)  # type: ignore[arg-type]
