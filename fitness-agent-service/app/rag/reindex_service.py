"""管理员可复现知识索引重建编排。"""

from __future__ import annotations

import asyncio
from secrets import token_hex

from app.infrastructure.agent_context import AgentIdentity

from .admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeReindexJob,
    KnowledgeReindexNotFound,
)
from .ingestion import DocumentIngestionService, IngestionRequest
from .reindex_repository import KnowledgeReindexRepository
from .storage import DocumentStorage

ADMIN_ROLES = frozenset({"ADMIN", "ORG_ADMIN", "SUPER_ADMIN"})
PLATFORM_ADMIN_ROLES = frozenset({"ADMIN", "SUPER_ADMIN"})


class KnowledgeReindexService:
    """创建、授权、执行并重试持久化的索引重建批次。

    索引重建有意复用普通入库的解析器、父子分块器和 Embedding 路径。唯一差异是使用
    ``force=True``：原有文档版本会被原子替换，模型或分块策略调整不会产生虚假的文档版本。
    """

    def __init__(
        self,
        jobs: KnowledgeReindexRepository,
        ingestion: DocumentIngestionService,
        storage: DocumentStorage,
        *,
        max_attempts: int = 3,
        item_batch_size: int = 10,
    ) -> None:
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("re-index max attempts must be between 1 and 5")
        if item_batch_size < 1 or item_batch_size > 100:
            raise ValueError("re-index item batch size must be between 1 and 100")
        self.jobs = jobs
        self.ingestion = ingestion
        self.storage = storage
        self.max_attempts = max_attempts
        self.item_batch_size = item_batch_size

    async def create_job(
        self,
        identity: AgentIdentity,
        *,
        organization_id: str | None,
        document_id: str | None,
    ) -> KnowledgeReindexJob:
        """在加入队列前，对已授权的重建范围进行快照。"""

        self.require_admin(identity)
        platform_wide = bool(PLATFORM_ADMIN_ROLES.intersection(identity.roles))
        if not platform_wide:
            if organization_id is None:
                raise ValueError("organization_id is required for an organization administrator")
            if organization_id not in identity.organization_ids:
                raise KnowledgeAdminForbidden("re-index scope is outside the signed admin scope")

        sources = await self.jobs.list_sources(
            organization_id=organization_id,
            document_id=document_id,
        )
        if not sources:
            raise KnowledgeReindexNotFound("no published document is available for re-indexing")
        job = KnowledgeReindexJob(
            id=token_hex(16),
            requested_by=identity.subject,
            organization_id=organization_id,
            target_document_id=document_id,
            status="QUEUED",
            total_documents=len(sources),
            processed_documents=0,
            succeeded_documents=0,
            skipped_documents=0,
            failed_documents=0,
            attempt_count=0,
            max_attempts=self.max_attempts,
        )
        return await self.jobs.create_job(
            job=job,
            sources=sources,
            item_ids=[token_hex(16) for _ in sources],
        )

    async def get_job(self, identity: AgentIdentity, job_id: str) -> KnowledgeReindexJob:
        self.require_admin(identity)
        job = await self.jobs.get_job(job_id)
        self._require_scope(identity, job)
        return job

    async def list_jobs(
        self, identity: AgentIdentity, *, limit: int = 50
    ) -> list[KnowledgeReindexJob]:
        self.require_admin(identity)
        return await self.jobs.list_jobs(
            organization_ids=identity.organization_ids,
            platform_wide=bool(PLATFORM_ADMIN_ROLES.intersection(identity.roles)),
            limit=limit,
        )

    async def retry(self, identity: AgentIdentity, job_id: str) -> KnowledgeReindexJob:
        self.require_admin(identity)
        job = await self.jobs.get_job(job_id)
        self._require_scope(identity, job)
        return await self.jobs.retry(job_id)

    async def process_job(self, job_id: str) -> None:
        """处理已认领批次；每个项目都有独立的持久化结果。"""

        job = await self.jobs.claim_job(job_id)
        if job is None:
            return
        while item_ids := await self.jobs.list_pending_item_ids(job.id, limit=self.item_batch_size):
            for item_id in item_ids:
                item = await self.jobs.claim_item(item_id)
                if item is None:
                    continue
                try:
                    content = await asyncio.to_thread(self.storage.read, item.storage_key)
                    result = await self.ingestion.ingest_file(
                        IngestionRequest(
                            source_uri=item.source_uri,
                            title=item.title,
                            document_type=item.document_type,
                            raw_content="",
                            organization_id=item.organization_id,
                            owner_user_id=item.owner_user_id,
                            visibility=item.visibility,
                            allowed_roles=item.allowed_roles,
                            version=item.version,
                            effective_from=item.effective_from,
                            effective_to=item.effective_to,
                        ),
                        file_name=item.original_filename,
                        content=content,
                        force=True,
                    )
                    await self.jobs.complete_item(
                        item.id, skipped=result.status == "SKIPPED_UNCHANGED"
                    )
                except Exception as exc:  # noqa: BLE001 - 必须持久化项目级失败状态
                    await self.jobs.fail_item(
                        item.id,
                        error_message=str(exc) or "re-index item failed",
                    )
        await self.jobs.finalize_job(job.id)

    @staticmethod
    def require_admin(identity: AgentIdentity) -> None:
        """只使用签名上下文中的角色；重建范围绝不来自模型或上传文件。"""

        if not ADMIN_ROLES.intersection(identity.roles):
            raise KnowledgeAdminForbidden("administrator role is required")

    @staticmethod
    def _require_scope(identity: AgentIdentity, job: KnowledgeReindexJob) -> None:
        if PLATFORM_ADMIN_ROLES.intersection(identity.roles):
            return
        if job.organization_id in identity.organization_ids:
            return
        raise KnowledgeAdminForbidden("re-index job is outside the signed admin scope")
