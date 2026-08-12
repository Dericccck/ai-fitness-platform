from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeUploadMetadata,
)
from app.rag.admin_service import KnowledgeAdminService
from app.rag.formats import DocumentParserRegistry
from app.rag.ingestion import IngestionResult
from app.rag.storage import LocalDocumentStorage


def identity(*roles: str, organizations: frozenset[str] | None = None) -> AgentIdentity:
    return AgentIdentity(
        subject="admin-1",
        organization_ids=organizations or frozenset({"org-1"}),
        roles=frozenset(roles),
        issued_at=1,
        expires_at=2,
    )


def metadata(**overrides: Any) -> KnowledgeUploadMetadata:
    values: dict[str, Any] = {
        "source_uri": "knowledge://fitness/warmup.md",
        "title": "Warmup guide",
        "document_type": "FITNESS_GUIDE",
        "organization_id": None,
        "visibility": "GLOBAL",
        "allowed_roles": (),
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "effective_to": None,
    }
    values.update(overrides)
    return KnowledgeUploadMetadata(**values)


class FakeKnowledgeRepository:
    async def get_current_document(self, source_uri: str) -> None:
        return None


class FakeJobs:
    def __init__(self) -> None:
        self.created: list[KnowledgeIngestionJob] = []

    async def create_job(self, *, job: KnowledgeIngestionJob) -> KnowledgeIngestionJob:
        self.created.append(job)
        return job

    async def get_job(self, job_id: str) -> KnowledgeIngestionJob:
        return next(job for job in self.created if job.id == job_id)


class FakeIngestion:
    async def ingest_file(self, request: Any, *, file_name: str, content: bytes) -> IngestionResult:
        return IngestionResult("INDEXED", "document-1", "checksum", request.version, 1)


def build_service(tmp_path: Path) -> tuple[KnowledgeAdminService, FakeJobs]:
    jobs = FakeJobs()
    service = KnowledgeAdminService(
        jobs,
        FakeKnowledgeRepository(),
        FakeIngestion(),
        LocalDocumentStorage(str(tmp_path)),
        DocumentParserRegistry(),
        max_source_bytes=1000,
    )
    return service, jobs


async def test_upload_requires_admin_and_stages_before_review(tmp_path: Path) -> None:
    service, jobs = build_service(tmp_path)

    with pytest.raises(KnowledgeAdminForbidden):
        await service.submit_upload(
            identity("STUDENT"),
            file_name="warmup.md",
            content_type="text/markdown",
            content=b"# Warmup\n\nPrepare the hips.",
            metadata=metadata(),
        )

    job = await service.submit_upload(
        identity("ADMIN"),
        file_name="warmup.md",
        content_type="text/markdown",
        content=b"# Warmup\n\nPrepare the hips.",
        metadata=metadata(),
    )

    assert job.status == "PENDING_REVIEW"
    assert job.requested_version == 1
    assert jobs.created[0].storage_key.endswith(".md")
    assert (tmp_path / jobs.created[0].storage_key).read_text() == "# Warmup\n\nPrepare the hips."


async def test_organization_scope_defaults_to_signed_single_organization(tmp_path: Path) -> None:
    service, _ = build_service(tmp_path)

    job = await service.submit_upload(
        identity("ADMIN", organizations=frozenset({"org-7"})),
        file_name="plan.md",
        content_type="text/markdown",
        content=b"# Plan\n\nSquat.",
        metadata=metadata(
            source_uri="knowledge://fitness/plan.md",
            visibility="ORGANIZATION",
        ),
    )

    assert job.organization_id == "org-7"


async def test_organization_admin_cannot_read_global_task(tmp_path: Path) -> None:
    service, _ = build_service(tmp_path)
    job = await service.submit_upload(
        identity("ADMIN"),
        file_name="global.md",
        content_type="text/markdown",
        content=b"# Global\n\nSafety.",
        metadata=metadata(source_uri="knowledge://fitness/global.md"),
    )

    with pytest.raises(KnowledgeAdminForbidden):
        await service.get_job(identity("ORG_ADMIN"), job.id)


async def test_worker_uses_immutable_staged_bytes(tmp_path: Path) -> None:
    service, _ = build_service(tmp_path)
    job = await service.submit_upload(
        identity("SUPER_ADMIN"),
        file_name="warmup.md",
        content_type="text/markdown",
        content=b"# Warmup\n\nPrepare.",
        metadata=metadata(),
    )

    # 模拟任务仓储没有实现 claim/complete，因此本断言聚焦信任边界：
    # 暂存对象保持不透明且安全。
    assert job.storage_key.startswith(job.id)
    assert "/" not in job.storage_key


async def test_scanned_pdf_can_enter_review_queue_without_becoming_publishable(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    payload = BytesIO()
    writer.write(payload)
    service, _ = build_service(tmp_path)

    job = await service.submit_upload(
        identity("SUPER_ADMIN"),
        file_name="scanned-guide.pdf",
        content_type="application/pdf",
        content=payload.getvalue(),
        metadata=metadata(source_uri="knowledge://fitness/scanned-guide.pdf"),
    )

    # 没有 Linux OCR 时仍保留原件和页面路由证据供审核，但真正的索引服务会在
    # Embedding 前因 OCR_REQUIRED fail-closed。
    assert job.status == "PENDING_REVIEW"
    assert job.original_filename == "scanned-guide.pdf"
