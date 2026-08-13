from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeReindexItem,
    KnowledgeReindexJob,
    KnowledgeReindexSource,
)
from app.rag.ingestion import IngestionResult
from app.rag.reindex_service import KnowledgeReindexService
from app.rag.storage import LocalDocumentStorage


def identity(*roles: str, organizations: frozenset[str] | None = None) -> AgentIdentity:
    return AgentIdentity(
        subject="admin-1",
        organization_ids=organizations or frozenset({"org-1"}),
        roles=frozenset(roles),
        issued_at=1,
        expires_at=2,
    )


def source(*, organization_id: str | None = "org-1") -> KnowledgeReindexSource:
    return KnowledgeReindexSource(
        document_id="doc-1",
        source_uri="knowledge://fitness/guide.pdf",
        title="训练指南",
        document_type="FITNESS_GUIDE",
        organization_id=organization_id,
        owner_user_id=None,
        visibility="ORGANIZATION" if organization_id else "GLOBAL",
        allowed_roles=("COACH",),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=None,
        version=3,
        storage_key="job-1.pdf",
        original_filename="guide.pdf",
        content_type="application/pdf",
    )


class FakeJobs:
    def __init__(self, sources: list[KnowledgeReindexSource]) -> None:
        self.sources = sources
        self.created: KnowledgeReindexJob | None = None
        self.item = KnowledgeReindexItem(
            id="item-1",
            job_id="reindex-1",
            document_id="doc-1",
            source_uri="knowledge://fitness/guide.pdf",
            title="训练指南",
            document_type="FITNESS_GUIDE",
            organization_id="org-1",
            owner_user_id=None,
            visibility="ORGANIZATION",
            allowed_roles=("COACH",),
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            effective_to=None,
            version=3,
            storage_key="job-1.pdf",
            original_filename="guide.pdf",
            content_type="application/pdf",
            status="PENDING",
            attempt_count=0,
            max_attempts=3,
        )
        self.completed: list[bool] = []

    async def list_sources(
        self, *, organization_id: str | None = None, document_id: str | None = None
    ) -> list[KnowledgeReindexSource]:
        return [
            item
            for item in self.sources
            if (organization_id is None or item.organization_id == organization_id)
            and (document_id is None or item.document_id == document_id)
        ]

    async def create_job(
        self, *, job: KnowledgeReindexJob, sources: Any, item_ids: Any
    ) -> KnowledgeReindexJob:
        self.created = job
        return job

    async def get_job(self, job_id: str) -> KnowledgeReindexJob:
        assert self.created is not None
        return self.created

    async def claim_job(self, job_id: str) -> KnowledgeReindexJob | None:
        return KnowledgeReindexJob(
            id=job_id,
            requested_by="admin-1",
            organization_id="org-1",
            target_document_id=None,
            status="INDEXING",
            total_documents=1,
            processed_documents=0,
            succeeded_documents=0,
            skipped_documents=0,
            failed_documents=0,
            attempt_count=1,
            max_attempts=3,
        )

    async def list_pending_item_ids(self, job_id: str, *, limit: int) -> list[str]:
        return [self.item.id] if self.item.status == "PENDING" else []

    async def claim_item(self, item_id: str) -> KnowledgeReindexItem | None:
        self.item = KnowledgeReindexItem(**{**self.item.__dict__, "status": "INDEXING"})
        return self.item

    async def complete_item(self, item_id: str, *, skipped: bool) -> KnowledgeReindexItem:
        self.completed.append(skipped)
        self.item = KnowledgeReindexItem(
            **{**self.item.__dict__, "status": "SKIPPED" if skipped else "SUCCEEDED"}
        )
        return self.item

    async def fail_item(self, item_id: str, *, error_message: str) -> KnowledgeReindexItem:
        raise AssertionError(error_message)

    async def finalize_job(self, job_id: str) -> KnowledgeReindexJob:
        return KnowledgeReindexJob(
            id=job_id,
            requested_by="admin-1",
            organization_id="org-1",
            target_document_id=None,
            status="SUCCEEDED",
            total_documents=1,
            processed_documents=1,
            succeeded_documents=1,
            skipped_documents=0,
            failed_documents=0,
            attempt_count=1,
            max_attempts=3,
        )


class FakeIngestion:
    def __init__(self) -> None:
        self.force_values: list[bool] = []
        self.reviewed_pages: list[tuple[int, ...]] = []

    async def ingest_file(
        self,
        request: Any,
        *,
        file_name: str,
        content: bytes,
        force: bool = False,
        reviewed_visual_pages: tuple[int, ...] = (),
    ) -> IngestionResult:
        self.force_values.append(force)
        self.reviewed_pages.append(reviewed_visual_pages)
        return IngestionResult("INDEXED", "doc-1", "checksum", request.version, 2)


async def test_reindex_creation_requires_scoped_admin_and_snapshots_sources(tmp_path: Path) -> None:
    jobs = FakeJobs([source()])
    service = KnowledgeReindexService(
        jobs,
        FakeIngestion(),
        LocalDocumentStorage(str(tmp_path)),
    )

    with pytest.raises(KnowledgeAdminForbidden):
        await service.create_job(identity("STUDENT"), organization_id="org-1", document_id=None)
    with pytest.raises(KnowledgeAdminForbidden):
        await service.create_job(identity("ORG_ADMIN"), organization_id="org-2", document_id=None)

    job = await service.create_job(
        identity("ORG_ADMIN"), organization_id="org-1", document_id="doc-1"
    )

    assert job.status == "QUEUED"
    assert job.total_documents == 1
    assert jobs.created is job


async def test_reindex_worker_reuses_ingestion_with_force_flag(tmp_path: Path) -> None:
    jobs = FakeJobs([source()])
    ingestion = FakeIngestion()
    storage = LocalDocumentStorage(str(tmp_path))
    storage.store("job-1", "guide.pdf", b"not-used", content_type="application/pdf")
    service = KnowledgeReindexService(jobs, ingestion, storage)

    await service.process_job("reindex-1")

    assert ingestion.force_values == [True]
    assert ingestion.reviewed_pages == [()]
    assert jobs.completed == [False]
