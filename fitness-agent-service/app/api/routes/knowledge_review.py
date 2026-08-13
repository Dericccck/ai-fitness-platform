"""教练和专业人员使用的知识审核工作台 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.rag.admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeJobNotFound,
    KnowledgeJobTransitionError,
    KnowledgeReviewReportNotFound,
)
from app.rag.review_service import KnowledgeReviewCase
from app.rag.review_workflow import (
    KnowledgePublicationCredential,
    KnowledgeReviewDecision,
    KnowledgeReviewOutcome,
    KnowledgeReviewRegion,
    KnowledgeReviewRequirement,
)

router = APIRouter(prefix="/api/v1/knowledge-review", tags=["knowledge-review"])


class ReviewRegionRequest(BaseModel):
    """页面左上角为原点、宽高归一化到 0~1 的审核矩形。"""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    label: str | None = Field(default=None, max_length=200)


class ReviewDecisionRequest(BaseModel):
    """权限、角色和资质不在请求体中，全部来自签名 AgentContext。"""

    model_config = ConfigDict(extra="forbid")

    # 审核领域决定资质要求：普通健身内容由教练/内容审核人处理，临床安全必须有核验资质。
    review_domain: Literal[
        "FITNESS_COACHING_SAFETY",
        "FITNESS_CONTENT_REVIEW",
        "CLINICAL_EXERCISE_SAFETY",
    ]
    # APPROVED 仅解除本领域、指定页或指定文档范围；REJECTED 必须保留原因且不能发布。
    decision: Literal["APPROVED", "REJECTED"]
    # DOCUMENT 覆盖整份文档；PAGES 只覆盖机器报告要求的具体页码。
    scope_type: Literal["DOCUMENT", "PAGES"]
    page_numbers: tuple[int, ...] = Field(default=(), max_length=2000)
    regions: tuple[ReviewRegionRequest, ...] = Field(default=(), max_length=5000)
    comment: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def validate_page_shape(self) -> ReviewDecisionRequest:
        if self.page_numbers != tuple(sorted(set(self.page_numbers))):
            raise ValueError("page_numbers must be unique and sorted")
        return self


class ReviewRequirementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    scope_type: str
    page_numbers: tuple[int, ...]
    finding_codes: tuple[str, ...]


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    report_id: str
    job_id: str
    review_domain: str
    decision: str
    scope_type: str
    page_numbers: tuple[int, ...]
    regions: tuple[ReviewRegionRequest, ...]
    finding_codes: tuple[str, ...]
    reviewer_id: str
    reviewer_roles: tuple[str, ...]
    reviewer_capabilities: tuple[str, ...]
    reviewer_qualifications: tuple[str, ...]
    reviewer_organization_ids: tuple[str, ...]
    comment: str
    created_at: datetime | None


class PublicationCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    job_id: str
    report_id: str
    report_version: int
    document_sha256: str
    parser_pipeline_version: str
    review_policy_version: str
    decision_ids: tuple[str, ...]
    approved_visual_pages: tuple[int, ...]
    issued_at: datetime | None
    revoked_at: datetime | None


class KnowledgeReviewCaseResponse(BaseModel):
    """不返回解析正文；审核人从独立原件接口读取哈希绑定的暂存文件。"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    title: str
    original_filename: str
    content_type: str
    visibility: str
    organization_id: str | None
    document_sha256: str
    report_id: str
    report_version: int
    report_status: str
    parser_pipeline_version: str
    review_policy_version: str
    requirements: tuple[ReviewRequirementResponse, ...]
    decisions: tuple[ReviewDecisionResponse, ...]
    publication_credential: PublicationCredentialResponse | None


class ReviewDecisionOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecisionResponse
    publication_credential: PublicationCredentialResponse | None


@router.get("/jobs/{job_id}", response_model=KnowledgeReviewCaseResponse)
async def get_review_case(
    job_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> KnowledgeReviewCaseResponse:
    """读取获授权的审核案件摘要、准确范围和当前决定。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        case = await request.app.state.knowledge_review.get_case(identity, job_id)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (KnowledgeJobNotFound, KnowledgeReviewReportNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _case_response(case)


@router.get("/jobs/{job_id}/source")
async def read_review_source(
    job_id: str,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> Response:
    """下载上传时的不可变原件，供 PDF/DOCX 审核工作台渲染。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        job, content = await request.app.state.knowledge_review.read_source(identity, job_id)
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    safe_name = quote(job.original_filename.replace("\r", "").replace("\n", ""), safe="")
    return Response(
        content=content,
        media_type=job.content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{safe_name}"},
    )


@router.post(
    "/jobs/{job_id}/decisions",
    response_model=ReviewDecisionOutcomeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_review_decision(
    job_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> ReviewDecisionOutcomeResponse:
    """提交一个领域的终局决定；全部通过时事务内自动签发发布凭证。"""

    identity = _verify_identity(request, x_agent_context)
    try:
        outcome = await request.app.state.knowledge_review.submit_decision(
            identity,
            job_id,
            review_domain=payload.review_domain,
            decision=payload.decision,
            scope_type=payload.scope_type,
            page_numbers=payload.page_numbers,
            regions=tuple(
                KnowledgeReviewRegion(
                    page_number=item.page_number,
                    x=item.x,
                    y=item.y,
                    width=item.width,
                    height=item.height,
                    label=item.label,
                )
                for item in payload.regions
            ),
            comment=payload.comment,
        )
    except KnowledgeAdminForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except KnowledgeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeJobTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _outcome_response(outcome)


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


def _requirement_response(item: KnowledgeReviewRequirement) -> ReviewRequirementResponse:
    return ReviewRequirementResponse(
        domain=item.domain,
        scope_type=item.scope_type,
        page_numbers=item.page_numbers,
        finding_codes=item.finding_codes,
    )


def _decision_response(item: KnowledgeReviewDecision) -> ReviewDecisionResponse:
    return ReviewDecisionResponse(
        id=item.id,
        report_id=item.report_id,
        job_id=item.job_id,
        review_domain=item.review_domain,
        decision=item.decision,
        scope_type=item.scope_type,
        page_numbers=item.page_numbers,
        regions=tuple(
            ReviewRegionRequest(
                page_number=region.page_number,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                label=region.label,
            )
            for region in item.regions
        ),
        finding_codes=item.finding_codes,
        reviewer_id=item.reviewer_id,
        reviewer_roles=item.reviewer_roles,
        reviewer_capabilities=item.reviewer_capabilities,
        reviewer_qualifications=item.reviewer_qualifications,
        reviewer_organization_ids=item.reviewer_organization_ids,
        comment=item.comment,
        created_at=item.created_at,
    )


def _credential_response(
    item: KnowledgePublicationCredential | None,
) -> PublicationCredentialResponse | None:
    if item is None:
        return None
    return PublicationCredentialResponse(
        id=item.id,
        job_id=item.job_id,
        report_id=item.report_id,
        report_version=item.report_version,
        document_sha256=item.document_sha256,
        parser_pipeline_version=item.parser_pipeline_version,
        review_policy_version=item.review_policy_version,
        decision_ids=item.decision_ids,
        approved_visual_pages=item.approved_visual_pages,
        issued_at=item.issued_at,
        revoked_at=item.revoked_at,
    )


def _case_response(case: KnowledgeReviewCase) -> KnowledgeReviewCaseResponse:
    return KnowledgeReviewCaseResponse(
        job_id=case.job.id,
        title=case.job.title,
        original_filename=case.job.original_filename,
        content_type=case.job.content_type,
        visibility=case.job.visibility,
        organization_id=case.job.organization_id,
        document_sha256=case.report.document_sha256,
        report_id=case.report.id,
        report_version=case.report.report_version,
        report_status=case.report.status,
        parser_pipeline_version=case.report.parser_pipeline_version,
        review_policy_version=case.report.review_policy_version,
        requirements=tuple(_requirement_response(item) for item in case.requirements),
        decisions=tuple(_decision_response(item) for item in case.decisions),
        publication_credential=_credential_response(case.publication_credential),
    )


def _outcome_response(outcome: KnowledgeReviewOutcome) -> ReviewDecisionOutcomeResponse:
    return ReviewDecisionOutcomeResponse(
        decision=_decision_response(outcome.decision),
        publication_credential=_credential_response(outcome.publication_credential),
    )
