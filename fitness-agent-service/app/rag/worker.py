"""可恢复知识索引任务轮询 Worker 的入口。"""

from __future__ import annotations

from dataclasses import dataclass

from .admin_repository import KnowledgeIngestionRepository
from .admin_service import KnowledgeAdminService


@dataclass(frozen=True)
class WorkerRunResult:
    """一次有界 Worker 轮询的可观测结果。"""

    discovered: int
    attempted: int


class KnowledgeIngestionWorker:
    """通过与 API 后台任务相同的原子认领路径处理排队任务。

    这个类有意与 FastAPI 分离，可由 Kubernetes CronJob、长期运行的 Worker Deployment
    或未来的 Redis/队列消费者启动。即使存在多个 Worker 也不会重复执行，因为 PostgreSQL
    只允许一个 Worker 成功认领 ``QUEUED`` 任务。
    """

    def __init__(
        self,
        jobs: KnowledgeIngestionRepository,
        service: KnowledgeAdminService,
        *,
        batch_size: int = 10,
    ) -> None:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("Worker 批次大小必须在 1 到 100 之间")
        self.jobs = jobs
        self.service = service
        self.batch_size = batch_size

    async def run_once(self) -> WorkerRunResult:
        """轮询一个有界批次，并让每个任务独立持久化成功或失败状态。"""

        job_ids = await self.jobs.list_queued_ids(limit=self.batch_size)
        for job_id in job_ids:
            await self.service.process_job(job_id)
        return WorkerRunResult(discovered=len(job_ids), attempted=len(job_ids))
