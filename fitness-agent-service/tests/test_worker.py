from app.rag.worker import KnowledgeIngestionWorker


class FakeJobs:
    async def list_queued_ids(self, *, limit: int) -> list[str]:
        assert limit == 2
        return ["job-1", "job-2"]


class FakeService:
    def __init__(self) -> None:
        self.processed: list[str] = []

    async def process_job(self, job_id: str) -> None:
        self.processed.append(job_id)


async def test_worker_processes_bounded_queued_batch() -> None:
    service = FakeService()
    worker = KnowledgeIngestionWorker(FakeJobs(), service, batch_size=2)

    result = await worker.run_once()

    assert result.discovered == 2
    assert result.attempted == 2
    assert service.processed == ["job-1", "job-2"]
