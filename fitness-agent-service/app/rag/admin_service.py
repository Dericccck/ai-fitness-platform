"""Review-gated knowledge upload and indexing orchestration."""

from __future__ import annotations

import re
from secrets import token_hex

from app.infrastructure.agent_context import AgentIdentity

from .admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeUploadMetadata,
)
from .admin_repository import KnowledgeIngestionRepository
from .formats import DocumentParserRegistry
from .ingestion import DocumentIngestionService, IngestionRequest
from .repository import KnowledgeRepository
from .storage import LocalDocumentStorage

ADMIN_ROLES = frozenset({"ADMIN", "ORG_ADMIN", "SUPER_ADMIN"})
PLATFORM_ADMIN_ROLES = frozenset({"ADMIN", "SUPER_ADMIN"})
_SAFE_TEXT = re.compile(r"^[^\r\n]{1,256}$")


class KnowledgeAdminService:
    """Coordinate authorization, staging, review transitions, and indexing tasks.

    This service deliberately does not let an LLM participate in document publishing.
    A signed administrator identity submits/reviews the file, while the ingestion service
    remains responsible for parsing, Embedding, parent-child construction, and publication.
    """

    def __init__(
        self,
        jobs: KnowledgeIngestionRepository,
        knowledge_repository: KnowledgeRepository,
        ingestion: DocumentIngestionService,
        storage: LocalDocumentStorage,
        parser_registry: DocumentParserRegistry,
        *,
        max_source_bytes: int,
        max_attempts: int = 3,
    ) -> None:
        self.jobs = jobs
        self.knowledge_repository = knowledge_repository
        self.ingestion = ingestion
        self.storage = storage
        self.parser_registry = parser_registry
        self.max_source_bytes = max_source_bytes
        self.max_attempts = max_attempts

    async def submit_upload(
        self,
        identity: AgentIdentity,
        *,
        file_name: str,
        content_type: str | None,
        content: bytes,
        metadata: KnowledgeUploadMetadata,
    ) -> KnowledgeIngestionJob:
        """Validate an admin upload, stage it privately, and create a review task."""

        self.require_admin(identity)
        self._validate_metadata(identity, metadata)
        if not content:
            raise ValueError("uploaded document must not be empty")
        if len(content) > self.max_source_bytes:
            raise ValueError("uploaded document exceeds the configured size limit")
        # Parse once at the trust boundary. The later worker reparses the immutable bytes,
        # so a malformed file cannot sit indefinitely in the review queue unnoticed.
        self.parser_registry.parse(content, file_name=file_name)

        current = await self.knowledge_repository.get_current_document(metadata.source_uri)
        requested_version = 1 if current is None else current.version + 1
        organization_id = metadata.organization_id
        if metadata.visibility == "ORGANIZATION" and organization_id is None:
            organization_id = next(iter(identity.organization_ids))
        job_id = token_hex(16)
        storage_key = self.storage.store(job_id, file_name, content)
        job = KnowledgeIngestionJob(
            id=job_id,
            source_uri=metadata.source_uri,
            original_filename=file_name,
            storage_key=storage_key,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            title=metadata.title,
            document_type=metadata.document_type,
            organization_id=organization_id,
            owner_user_id=identity.subject if metadata.visibility == "PRIVATE" else None,
            visibility=metadata.visibility,
            allowed_roles=metadata.allowed_roles,
            effective_from=metadata.effective_from,
            effective_to=metadata.effective_to,
            requested_version=requested_version,
            submitted_by=identity.subject,
            status="PENDING_REVIEW",
            attempt_count=0,
            max_attempts=self.max_attempts,
        )
        return await self.jobs.create_job(job=job)

    async def approve(
        self, identity: AgentIdentity, job_id: str, *, comment: str | None = None
    ) -> KnowledgeIngestionJob:
        """Approve a pending task; the route schedules the actual worker after this call."""

        self.require_admin(identity)
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.approve(job_id, reviewer_id=identity.subject, comment=comment)

    async def reject(
        self, identity: AgentIdentity, job_id: str, *, comment: str
    ) -> KnowledgeIngestionJob:
        """Reject a task while retaining evidence and the reviewer decision."""

        self.require_admin(identity)
        if not comment.strip():
            raise ValueError("rejection comment is required")
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.reject(job_id, reviewer_id=identity.subject, comment=comment[:500])

    async def retry(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """Manually requeue a failed task within its bounded retry budget."""

        self.require_admin(identity)
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.retry(job_id, reviewer_id=identity.subject)

    async def get_job(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """Return task state only to administrators; content is never returned here."""

        self.require_admin(identity)
        return await self._get_scoped_job(identity, job_id)

    async def list_jobs(
        self, identity: AgentIdentity, *, limit: int = 50
    ) -> list[KnowledgeIngestionJob]:
        """Return bounded task summaries for an admin dashboard."""

        self.require_admin(identity)
        return await self.jobs.list_jobs(
            organization_ids=identity.organization_ids,
            platform_wide=bool(PLATFORM_ADMIN_ROLES.intersection(identity.roles)),
            limit=limit,
        )

    async def process_job(self, job_id: str) -> None:
        """Run one queued task and persist success/failure without leaking raw document text."""

        job = await self.jobs.claim(job_id)
        if job is None:
            return
        try:
            content = self.storage.read(job.storage_key)
            result = await self.ingestion.ingest_file(
                IngestionRequest(
                    source_uri=job.source_uri,
                    title=job.title,
                    document_type=job.document_type,
                    raw_content="",
                    organization_id=job.organization_id,
                    owner_user_id=job.owner_user_id,
                    visibility=job.visibility,
                    allowed_roles=job.allowed_roles,
                    version=job.requested_version,
                    effective_from=job.effective_from,
                    effective_to=job.effective_to,
                ),
                file_name=job.original_filename,
                content=content,
            )
            await self.jobs.complete(job_id, document_id=result.document_id)
        except Exception as exc:  # noqa: BLE001 - worker must persist a stable failure state
            await self.jobs.fail(
                job_id,
                error_code=type(exc).__name__,
                error_message=str(exc) or "indexing task failed",
            )

    @staticmethod
    def require_admin(identity: AgentIdentity) -> None:
        """Enforce admin roles from the signed context, never from multipart form data."""

        if not ADMIN_ROLES.intersection(identity.roles):
            raise KnowledgeAdminForbidden("administrator role is required")

    async def _get_scoped_job(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """Apply task scope before returning or transitioning a job."""

        job = await self.jobs.get_job(job_id)
        if PLATFORM_ADMIN_ROLES.intersection(identity.roles):
            return job
        if (
            job.visibility == "ORGANIZATION"
            and job.organization_id is not None
            and job.organization_id in identity.organization_ids
        ):
            return job
        raise KnowledgeAdminForbidden("knowledge task is outside the signed admin scope")

    @staticmethod
    def _validate_metadata(identity: AgentIdentity, metadata: KnowledgeUploadMetadata) -> None:
        if not _SAFE_TEXT.fullmatch(metadata.source_uri) or not metadata.source_uri.startswith(
            "knowledge://"
        ):
            raise ValueError("source_uri must be a safe knowledge:// URI")
        if not _SAFE_TEXT.fullmatch(metadata.title):
            raise ValueError("title is invalid")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", metadata.document_type):
            raise ValueError("document_type must be uppercase and bounded")
        if metadata.visibility == "GLOBAL" and metadata.organization_id is not None:
            raise ValueError("global documents cannot carry an organization scope")
        if metadata.visibility == "GLOBAL" and not PLATFORM_ADMIN_ROLES.intersection(
            identity.roles
        ):
            raise KnowledgeAdminForbidden("global knowledge requires a platform administrator")
        if metadata.visibility == "ORGANIZATION":
            if metadata.organization_id is None:
                if len(identity.organization_ids) != 1:
                    raise ValueError("organization_id is required for multi-organization admins")
            elif metadata.organization_id not in identity.organization_ids:
                raise KnowledgeAdminForbidden("organization is outside the signed admin scope")
        if metadata.effective_to is not None and metadata.effective_to <= metadata.effective_from:
            raise ValueError("effective_to must be after effective_from")
