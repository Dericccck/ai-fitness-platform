"""健身知识的专业审核决定、范围校验和发布凭证。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.infrastructure.agent_context import AgentIdentity

from .admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeJobTransitionError,
)
from .review import KnowledgeReviewReport

# 审核领域决定“谁有资格审核”：健身教练安全、健身内容、临床运动安全。
ReviewDomain = Literal[
    "FITNESS_COACHING_SAFETY",
    "FITNESS_CONTENT_REVIEW",
    "CLINICAL_EXERCISE_SAFETY",
]

# 专业审核决定：APPROVED 解除对应审核范围；REJECTED 保留拒绝原因且不能生成发布凭证。
ReviewDecisionValue = Literal["APPROVED", "REJECTED"]

# DOCUMENT 表示覆盖整份文档；PAGES 表示只覆盖报告明确列出的页码和区域。
ReviewScopeType = Literal["DOCUMENT", "PAGES"]

FITNESS_REVIEW_CAPABILITY = "KNOWLEDGE_REVIEW_FITNESS"
CLINICAL_REVIEW_CAPABILITY = "KNOWLEDGE_REVIEW_CLINICAL"
GLOBAL_REVIEW_CAPABILITY = "KNOWLEDGE_REVIEW_GLOBAL"
HEALTH_PROFESSIONAL_QUALIFICATION = "VERIFIED_HEALTH_PROFESSIONAL"


@dataclass(frozen=True)
class KnowledgeReviewRegion:
    """审核人在 PDF 页面上圈定的归一化矩形证据。"""

    page_number: int
    x: float
    y: float
    width: float
    height: float
    label: str | None = None


@dataclass(frozen=True)
class KnowledgeReviewRequirement:
    """由不可变报告推导出的一个必需审核范围。"""

    domain: ReviewDomain
    scope_type: ReviewScopeType
    page_numbers: tuple[int, ...]
    finding_codes: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeReviewDecision:
    """审核人的追加式决定，保存签名身份在决定时刻的授权快照。"""

    id: str
    report_id: str
    job_id: str
    review_domain: ReviewDomain
    decision: ReviewDecisionValue
    scope_type: ReviewScopeType
    page_numbers: tuple[int, ...]
    regions: tuple[KnowledgeReviewRegion, ...]
    finding_codes: tuple[str, ...]
    reviewer_id: str
    reviewer_roles: tuple[str, ...]
    reviewer_capabilities: tuple[str, ...]
    reviewer_qualifications: tuple[str, ...]
    reviewer_organization_ids: tuple[str, ...]
    comment: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class KnowledgePublicationCredential:
    """不是客户端令牌，而是 Worker 可核验的数据库发布授权证据。"""

    id: str
    job_id: str
    report_id: str
    report_version: int
    document_sha256: str
    parser_pipeline_version: str
    review_policy_version: str
    decision_ids: tuple[str, ...]
    approved_visual_pages: tuple[int, ...]
    issued_at: datetime | None = None
    revoked_at: datetime | None = None

    def validates(self, report: KnowledgeReviewReport, job: KnowledgeIngestionJob) -> bool:
        """凭证必须仍绑定同一任务、报告、字节哈希和当前策略版本。"""

        return (
            self.revoked_at is None
            and report.is_current
            and report.status == "REVIEW_REQUIRED"
            and self.job_id == job.id == report.job_id
            and self.report_id == report.id
            and self.report_version == report.report_version
            and self.document_sha256 == job.content_sha256 == report.document_sha256
            and self.parser_pipeline_version == report.parser_pipeline_version
            and self.review_policy_version == report.review_policy_version
        )


@dataclass(frozen=True)
class KnowledgeReviewOutcome:
    decision: KnowledgeReviewDecision
    publication_credential: KnowledgePublicationCredential | None


def review_requirements(report: KnowledgeReviewReport) -> tuple[KnowledgeReviewRequirement, ...]:
    """把报告领域转换为不可含糊的文档级或页级覆盖要求。"""

    results: list[KnowledgeReviewRequirement] = []
    for raw_domain in report.required_review_domains:
        if raw_domain not in {
            "FITNESS_COACHING_SAFETY",
            "FITNESS_CONTENT_REVIEW",
            "CLINICAL_EXERCISE_SAFETY",
        }:
            raise KnowledgeJobTransitionError(f"不支持的审查领域：{raw_domain}")
        domain: ReviewDomain = raw_domain  # type: ignore[assignment]
        matching = tuple(
            finding
            for finding in report.findings
            if _finding_belongs_to_domain(finding.code, domain)
        )
        # 训练方法、来源人工复核和医疗风险都必须通读全文。只有单纯的图片动作信息
        # 才允许按报告列出的页面完成审核，避免审核人用一页批准整份训练指南。
        requires_document = domain != "FITNESS_COACHING_SAFETY" or any(
            not finding.pages for finding in matching
        )
        pages = tuple(sorted({page for finding in matching for page in finding.pages}))
        results.append(
            KnowledgeReviewRequirement(
                domain=domain,
                scope_type="DOCUMENT" if requires_document or not pages else "PAGES",
                page_numbers=() if requires_document or not pages else pages,
                finding_codes=tuple(sorted(finding.code for finding in matching)),
            )
        )
    return tuple(results)


def validate_reviewer(
    identity: AgentIdentity,
    job: KnowledgeIngestionJob,
    requirement: KnowledgeReviewRequirement,
) -> None:
    """基于签名 claims 和任务租户执行职责分离，拒绝表单自报资质。"""

    if identity.subject == job.submitted_by:
        raise KnowledgeAdminForbidden("上传者不能审查同一知识版本")
    if job.visibility == "ORGANIZATION":
        if not job.organization_id or job.organization_id not in identity.organization_ids:
            raise KnowledgeAdminForbidden("审查任务不在已签名机构范围内")
    elif GLOBAL_REVIEW_CAPABILITY not in identity.capabilities:
        # 全局和私有知识都不能由任意组织教练批准。私有高风险资料暂时也走平台审核，
        # 直到学员-专业人员指派模型进入 Java 业务事实库。
        raise KnowledgeAdminForbidden("需要平台知识审查能力")

    if requirement.domain == "CLINICAL_EXERCISE_SAFETY":
        if CLINICAL_REVIEW_CAPABILITY not in identity.capabilities:
            raise KnowledgeAdminForbidden("需要临床知识审查能力")
        if HEALTH_PROFESSIONAL_QUALIFICATION not in identity.qualifications:
            raise KnowledgeAdminForbidden("需要经过验证的健康专业资质")
        return
    if "COACH" not in identity.roles:
        raise KnowledgeAdminForbidden("健身知识审查需要教练角色")
    if FITNESS_REVIEW_CAPABILITY not in identity.capabilities:
        raise KnowledgeAdminForbidden("需要健身知识审查能力")


def validate_decision_scope(
    requirement: KnowledgeReviewRequirement,
    scope_type: ReviewScopeType,
    page_numbers: tuple[int, ...],
    regions: tuple[KnowledgeReviewRegion, ...],
    *,
    total_pages: int,
) -> None:
    """要求决定精确覆盖机器报告范围，不允许用模糊页码或越界区域冒充审核。"""

    normalized_pages = tuple(sorted(set(page_numbers)))
    if normalized_pages != page_numbers:
        raise ValueError("page_numbers 必须唯一且已排序")
    if any(page < 1 or page > total_pages for page in page_numbers):
        raise ValueError("审查页不在已解析文档范围内")
    if scope_type != requirement.scope_type or page_numbers != requirement.page_numbers:
        raise ValueError("决定范围必须完全覆盖所需审查范围")
    if scope_type == "DOCUMENT" and regions:
        raise ValueError("文档级决定不能包含页面区域")
    for region in regions:
        if region.page_number not in page_numbers:
            raise ValueError("审查区域必须属于已批准页面")
        if (
            region.x < 0
            or region.y < 0
            or region.width <= 0
            or region.height <= 0
            or region.x + region.width > 1
            or region.y + region.height > 1
        ):
            raise ValueError("审查区域必须使用页面内的归一化坐标")


def approved_visual_pages(report: KnowledgeReviewReport) -> tuple[int, ...]:
    """发布凭证只解除人工视觉审核；OCR 阻断永远不能由人审凭证绕过。"""

    return tuple(
        sorted(
            {
                page
                for finding in report.findings
                if finding.code == "FITNESS_VISUAL_REVIEW_REQUIRED"
                for page in finding.pages
            }
        )
    )


def _finding_belongs_to_domain(code: str, domain: ReviewDomain) -> bool:
    if domain == "CLINICAL_EXERCISE_SAFETY":
        return code in {"CLINICAL_REVIEW_REQUIRED", "DECLARED_MEDICAL_RISK_REVIEW_REQUIRED"}
    if domain == "FITNESS_CONTENT_REVIEW":
        return code == "SOURCE_REQUIRES_HUMAN_REVIEW"
    return code in {"FITNESS_VISUAL_REVIEW_REQUIRED", "FITNESS_COACH_REVIEW_REQUIRED"}
