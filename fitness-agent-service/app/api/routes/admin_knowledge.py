"""Administrator APIs for reviewable knowledge uploads and indexing tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.rag.admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeJobNotFound,
    KnowledgeJobTransitionError,
    KnowledgeUploadMetadata,
)

router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["admin-knowledge"])


class KnowledgeJobResponse(BaseModel):
    """Task state exposed to admins without returning staged document content."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source_uri: str
    original_filename: str
    content_type: str
    size_bytes: int
    title: str
    document_type: str
    organization_id: str | None
    visibility: str
    allowed_roles: tuple[str, ...]
    requested_version: int
    status: str
    attempt_count: int
    max_attempts: int
    reviewer_id: str | None
    review_comment: str | None
    error_code: str | None
    error_message: str | None
    document_id: str | None
    content_sha256: str
    safety_status: str
    scanner_name: str
    created_at: datetime | None
    updated_at: datetime | None
    reviewed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class RejectRequest(BaseModel):
    """A rejection must explain the governance decision for later audit."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1, max_length=500)


class ReviewRequest(BaseModel):
    """Optional approval comment retained with the task transition."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=500)


@router.post(
    "/documents", response_model=KnowledgeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),  # noqa: B008
    source_uri: str = Form(..., min_length=13, max_length=256),
    title: str = Form(..., min_length=1, max_length=256),
    document_type: str = Form(..., min_length=2, max_length=64),
    visibility: Literal["GLOBAL", "ORGANIZATION", "PRIVATE"] = Form(default="GLOBAL"),
    organization_id: str | None = Form(default=None, max_length=128),
    allowed_roles: str = Form(default="", max_length=512),
    effective_from: datetime = Form(...),  # noqa: B008
    effective_to: datetime | None = Form(default=None),  # noqa: B008
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """Stage one file and put it into the pending-review state."""

    identity = _verify_identity(request, x_agent_context)
    content = await _read_upload(file, request.app.state.settings.rag_max_source_bytes)
    metadata = KnowledgeUploadMetadata(
        source_uri=source_uri,
        title=title,
        document_type=document_type,
        organization_id=organization_id,
        visibility=visibility,
        allowed_roles=_parse_roles(allowed_roles),
        effective_from=effective_from,
        effective_to=effective_to,
    )
    try:
        job = await request.app.state.knowledge_admin.submit_upload(
            identity,
            file_name=file.filename or "",
            content_type=file.content_type,
            content=content,
            metadata=metadata,
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _to_response(job)


@router.get("/jobs", response_model=list[KnowledgeJobResponse])
async def list_jobs(
    request: Request,
    limit: int = 50,
    x_agent_context: str | None = Header(default=None),
) -> list[KnowledgeJobResponse]:
    """List bounded task summaries for the admin console."""

    identity = _verify_identity(request, x_agent_context)
    try:
        jobs = await request.app.state.knowledge_admin.list_jobs(identity, limit=limit)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return [_to_response(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=KnowledgeJobResponse)
async def get_job(
    job_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """Read one task state without exposing the staged source bytes."""

    identity = _verify_identity(request, x_agent_context)
    try:
        return _to_response(await request.app.state.knowledge_admin.get_job(identity, job_id))
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/approve", response_model=KnowledgeJobResponse)
async def approve_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ReviewRequest | None = None,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """Approve and enqueue a task; indexing runs after the HTTP response."""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_admin.approve(
            identity, job_id, comment=payload.comment if payload else None
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(request.app.state.knowledge_admin.process_job, job.id)
    return _to_response(job)


@router.post("/jobs/{job_id}/reject", response_model=KnowledgeJobResponse)
async def reject_job(
    job_id: str,
    payload: RejectRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """Reject a task and retain the reason in the audit record."""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_admin.reject(
            identity, job_id, comment=payload.comment
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(job)


@router.post("/jobs/{job_id}/retry", response_model=KnowledgeJobResponse)
async def retry_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """Requeue a failed task only while its configured retry budget remains."""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_admin.retry(identity, job_id)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(request.app.state.knowledge_admin.process_job, job.id)
    return _to_response(job)


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signed agent context is required"
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signed agent context"
        ) from exc


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read in bounded chunks so Content-Length cannot bypass the upload limit."""

    parts: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="uploaded document exceeds the configured size limit",
            )
        parts.append(chunk)
    return b"".join(parts)


def _parse_roles(raw_roles: str) -> tuple[str, ...]:
    roles = tuple(sorted({item.strip().upper() for item in raw_roles.split(",") if item.strip()}))
    if len(roles) > 20 or any(len(role) > 64 for role in roles):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="too many roles"
        )
    return roles


def _to_response(job: KnowledgeIngestionJob) -> KnowledgeJobResponse:
    return KnowledgeJobResponse(
        id=job.id,
        source_uri=job.source_uri,
        original_filename=job.original_filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
        title=job.title,
        document_type=job.document_type,
        organization_id=job.organization_id,
        visibility=job.visibility,
        allowed_roles=job.allowed_roles,
        requested_version=job.requested_version,
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        reviewer_id=job.reviewer_id,
        review_comment=job.review_comment,
        error_code=job.error_code,
        error_message=job.error_message,
        document_id=job.document_id,
        content_sha256=job.content_sha256,
        safety_status=job.safety_status,
        scanner_name=job.scanner_name,
        created_at=job.created_at,
        updated_at=job.updated_at,
        reviewed_at=job.reviewed_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
