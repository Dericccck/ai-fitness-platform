"""轮询执行排队中的知识索引重建批次。"""

from __future__ import annotations

from dataclasses import dataclass

from .reindex_repository import KnowledgeReindexRepository
from .reindex_service import KnowledgeReindexService


@dataclass(frozen=True)
class ReindexWorkerRunResult:
    """一次有界重建轮询的可观测结果。"""

    discovered: int
    attempted: int


class KnowledgeReindexWorker:
    """处理排队的重建批次，并通过数据库防止重复执行。"""

    def __init__(
        self,
        jobs: KnowledgeReindexRepository,
        service: KnowledgeReindexService,
        *,
        batch_size: int = 2,
    ) -> None:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("重新索引 Worker 批次大小必须在 1 到 100 之间")
        self.jobs = jobs
        self.service = service
        self.batch_size = batch_size

    async def run_once(self) -> ReindexWorkerRunResult:
        """认领并执行数量受限的排队重建批次。"""

        job_ids = await self.jobs.list_queued_ids(limit=self.batch_size)
        for job_id in job_ids:
            await self.service.process_job(job_id)
        return ReindexWorkerRunResult(discovered=len(job_ids), attempted=len(job_ids))
