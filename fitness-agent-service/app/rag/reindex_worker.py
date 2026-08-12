"""Polling worker for queued knowledge index rebuild batches."""

from __future__ import annotations

from dataclasses import dataclass

from .reindex_repository import KnowledgeReindexRepository
from .reindex_service import KnowledgeReindexService


@dataclass(frozen=True)
class ReindexWorkerRunResult:
    """Observable outcome of one bounded rebuild poll."""

    discovered: int
    attempted: int


class KnowledgeReindexWorker:
    """Process queued rebuild batches with database-backed duplicate protection."""

    def __init__(
        self,
        jobs: KnowledgeReindexRepository,
        service: KnowledgeReindexService,
        *,
        batch_size: int = 2,
    ) -> None:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("re-index worker batch size must be between 1 and 100")
        self.jobs = jobs
        self.service = service
        self.batch_size = batch_size

    async def run_once(self) -> ReindexWorkerRunResult:
        """Claim and execute a bounded number of queued rebuild batches."""

        job_ids = await self.jobs.list_queued_ids(limit=self.batch_size)
        for job_id in job_ids:
            await self.service.process_job(job_id)
        return ReindexWorkerRunResult(discovered=len(job_ids), attempted=len(job_ids))
