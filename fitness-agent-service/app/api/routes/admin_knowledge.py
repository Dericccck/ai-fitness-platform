"""支持审核的知识上传与索引任务管理员 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

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
    KnowledgeReindexJob,
    KnowledgeReindexNotFound,
    KnowledgeReviewReportNotFound,
    KnowledgeUploadMetadata,
)
from app.rag.ocr import OcrServiceUnavailable
from app.rag.review import KnowledgeReviewReport
from app.rag.safety import DocumentSecurityUnavailable

router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["admin-knowledge"])


class KnowledgeJobResponse(BaseModel):
    """向管理员暴露任务状态，但不返回暂存文档内容。"""

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
    malware_status: str
    malware_scanner: str
    malware_signature: str | None
    malware_scanned_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    reviewed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class RejectRequest(BaseModel):
    """拒绝操作必须说明治理决定，供后续审计。"""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1, max_length=500)


class ReviewRequest(BaseModel):
    """可选的审批备注，并随任务状态转换一同保留。"""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=500)


class PdfPageProfileResponse(BaseModel):
    """审核控制台展示的 PDF 页级证据，不包含原始正文。"""

    model_config = ConfigDict(extra="forbid")

    page_number: int
    image_count: int
    image_area_ratio: float
    native_text_chars: int
    text_area_ratio: float
    table_count: int
    caption_count: int
    route: str
    reasons: tuple[str, ...]


class ReviewFindingResponse(BaseModel):
    """机器可聚合、人工可理解的审核报告结论。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    message: str
    pages: tuple[int, ...]


class KnowledgeReviewReportResponse(BaseModel):
    """版本化解析审核报告；审批接口必须依据该报告执行 fail-closed。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    job_id: str
    report_version: int
    document_sha256: str
    parser_name: str
    parser_version: str
    parser_pipeline_version: str
    review_policy_version: str
    media_type: str
    declared_risk_level: str
    source_requires_human_review: bool
    status: str
    can_admin_approve: bool
    quality_metrics: dict[str, Any]
    page_profiles: tuple[PdfPageProfileResponse, ...]
    warnings: tuple[str, ...]
    findings: tuple[ReviewFindingResponse, ...]
    required_review_domains: tuple[str, ...]
    recommended_reviewer_roles: tuple[str, ...]
    required_qualifications: tuple[str, ...]
    created_at: datetime | None


class ReindexCreateRequest(BaseModel):
    """定义重建范围；权限从签名上下文中解析。"""

    model_config = ConfigDict(extra="forbid")

    organization_id: str | None = Field(default=None, max_length=128)
    document_id: str | None = Field(default=None, max_length=256)


class ReindexJobResponse(BaseModel):
    """向管理员看板暴露的进度计数器。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    requested_by: str
    organization_id: str | None
    target_document_id: str | None
    status: str
    total_documents: int
    processed_documents: int
    succeeded_documents: int
    skipped_documents: int
    failed_documents: int
    attempt_count: int
    max_attempts: int
    error_message: str | None
    created_at: datetime | None
    updated_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


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
    # NORMAL 普通健身内容；CAUTION 含明显安全提示；MEDICAL 涉及临床/疾病/体重管理，
    # 会触发更严格的专业审核，而不是只靠管理员勾选放行。
    risk_level: Literal["NORMAL", "CAUTION", "MEDICAL"] = Form(default="NORMAL"),
    requires_human_review: bool = Form(default=False),
    # GLOBAL 全平台；ORGANIZATION 机构内；PRIVATE 仅提交者/所有者。该值决定 SQL ACL 范围。
    visibility: Literal["GLOBAL", "ORGANIZATION", "PRIVATE"] = Form(default="GLOBAL"),
    organization_id: str | None = Form(default=None, max_length=128),
    allowed_roles: str = Form(default="", max_length=512),
    effective_from: datetime = Form(...),  # noqa: B008
    effective_to: datetime | None = Form(default=None),  # noqa: B008
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """暂存一个文件，并将其置于待审核状态。"""

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
        risk_level=risk_level,
        requires_human_review=requires_human_review,
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
    except DocumentSecurityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="malware scanner is temporarily unavailable",
        ) from exc
    except OcrServiceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR service is temporarily unavailable",
        ) from exc
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
    """为管理员控制台返回数量受限的任务摘要。"""

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
    """读取单个任务状态，但不暴露暂存源文件字节。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        return _to_response(await request.app.state.knowledge_admin.get_job(identity, job_id))
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/jobs/{job_id}/review-report",
    response_model=KnowledgeReviewReportResponse,
)
async def get_review_report(
    job_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeReviewReportResponse:
    """读取上传时生成的解析质量和专业审核路由证据。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        report, approval_ready = await request.app.state.knowledge_admin.get_review_report_status(
            identity, job_id
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (KnowledgeJobNotFound, KnowledgeReviewReportNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_review_report_response(report, approval_ready=approval_ready)


@router.post("/jobs/{job_id}/approve", response_model=KnowledgeJobResponse)
async def approve_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ReviewRequest | None = None,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeJobResponse:
    """批准并排队任务；索引会在 HTTP 响应返回后执行。"""

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
    """拒绝任务，并将原因保留在审计记录中。"""

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
    """只有在配置的重试额度未耗尽时，才重新排队失败任务。"""

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


@router.post(
    "/reindex/jobs",
    response_model=ReindexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_reindex_job(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ReindexCreateRequest,
    x_agent_context: str | None = Header(default=None),
) -> ReindexJobResponse:
    """创建快照并排队重建单个文档、单个组织或全部知识。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_reindex.create_job(
            identity,
            organization_id=payload.organization_id,
            document_id=payload.document_id,
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeReindexNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    background_tasks.add_task(request.app.state.knowledge_reindex.process_job, job.id)
    return _to_reindex_response(job)


@router.get("/reindex/jobs", response_model=list[ReindexJobResponse])
async def list_reindex_jobs(
    request: Request,
    limit: int = 50,
    x_agent_context: str | None = Header(default=None),
) -> list[ReindexJobResponse]:
    """只列出签名管理员有权限查看的重建批次。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        jobs = await request.app.state.knowledge_reindex.list_jobs(identity, limit=limit)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return [_to_reindex_response(job) for job in jobs]


@router.get("/reindex/jobs/{job_id}", response_model=ReindexJobResponse)
async def get_reindex_job(
    job_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> ReindexJobResponse:
    """读取计数器和最终状态，但不暴露文档内容。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_reindex.get_job(identity, job_id)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_reindex_response(job)


@router.post("/reindex/jobs/{job_id}/retry", response_model=ReindexJobResponse)
async def retry_reindex_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_agent_context: str | None = Header(default=None),
) -> ReindexJobResponse:
    """在失败文档项目仍有有限重试额度时重新排队。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        job = await request.app.state.knowledge_reindex.retry(identity, job_id)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(request.app.state.knowledge_reindex.process_job, job.id)
    return _to_reindex_response(job)


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
    """按有界分块读取，避免通过伪造 Content-Length 绕过上传限制。"""

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
        malware_status=job.malware_status,
        malware_scanner=job.malware_scanner,
        malware_signature=job.malware_signature,
        malware_scanned_at=job.malware_scanned_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        reviewed_at=job.reviewed_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _to_reindex_response(job: KnowledgeReindexJob) -> ReindexJobResponse:
    return ReindexJobResponse(
        id=job.id,
        requested_by=job.requested_by,
        organization_id=job.organization_id,
        target_document_id=job.target_document_id,
        status=job.status,
        total_documents=job.total_documents,
        processed_documents=job.processed_documents,
        succeeded_documents=job.succeeded_documents,
        skipped_documents=job.skipped_documents,
        failed_documents=job.failed_documents,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _to_review_report_response(
    report: KnowledgeReviewReport,
    *,
    approval_ready: bool | None = None,
) -> KnowledgeReviewReportResponse:
    """将内部领域对象转换为不含正文和存储键的管理员 API 结构。"""

    return KnowledgeReviewReportResponse(
        id=report.id,
        job_id=report.job_id,
        report_version=report.report_version,
        document_sha256=report.document_sha256,
        parser_name=report.parser_name,
        parser_version=report.parser_version,
        parser_pipeline_version=report.parser_pipeline_version,
        review_policy_version=report.review_policy_version,
        media_type=report.media_type,
        declared_risk_level=report.declared_risk_level,
        source_requires_human_review=report.source_requires_human_review,
        status=report.status,
        can_admin_approve=(report.can_admin_approve if approval_ready is None else approval_ready),
        quality_metrics=report.quality_metrics,
        page_profiles=tuple(
            PdfPageProfileResponse(
                page_number=profile.page_number,
                image_count=profile.image_count,
                image_area_ratio=profile.image_area_ratio,
                native_text_chars=profile.native_text_chars,
                text_area_ratio=profile.text_area_ratio,
                table_count=profile.table_count,
                caption_count=profile.caption_count,
                route=profile.route,
                reasons=profile.reasons,
            )
            for profile in report.page_profiles
        ),
        warnings=report.warnings,
        findings=tuple(
            ReviewFindingResponse(
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                pages=finding.pages,
            )
            for finding in report.findings
        ),
        required_review_domains=report.required_review_domains,
        recommended_reviewer_roles=report.recommended_reviewer_roles,
        required_qualifications=report.required_qualifications,
        created_at=report.created_at,
    )
