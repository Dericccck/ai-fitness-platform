"""Recoverable polling worker entry point for knowledge indexing tasks."""

from __future__ import annotations

from dataclasses import dataclass

from .admin_repository import KnowledgeIngestionRepository
from .admin_service import KnowledgeAdminService


@dataclass(frozen=True)
class WorkerRunResult:
    """Observable outcome of one bounded worker poll."""

    discovered: int
    attempted: int


class KnowledgeIngestionWorker:
    """Process queued jobs through the same atomic Claim path used by API background tasks.

    This class is intentionally a separate boundary from FastAPI. It can be launched by a
    Kubernetes CronJob, a long-running worker Deployment, or a future Redis/queue consumer;
    duplicate workers are safe because PostgreSQL accepts only one ``QUEUED`` Claim.
    """

    def __init__(
        self,
        jobs: KnowledgeIngestionRepository,
        service: KnowledgeAdminService,
        *,
        batch_size: int = 10,
    ) -> None:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("worker batch size must be between 1 and 100")
        self.jobs = jobs
        self.service = service
        self.batch_size = batch_size

    async def run_once(self) -> WorkerRunResult:
        """Poll a bounded batch and let each task persist its own success/failure state."""

        job_ids = await self.jobs.list_queued_ids(limit=self.batch_size)
        for job_id in job_ids:
            await self.service.process_job(job_id)
        return WorkerRunResult(discovered=len(job_ids), attempted=len(job_ids))
