"""健身知识上传后的解析质量报告与专业审核路由。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from .document_quality import (
    DocumentQualityMetrics,
    DocumentQualityThresholds,
    measure_document_quality,
)
from .formats import ParsedDocument, PdfPageProfile
from .ingestion import chunk_parsed_blocks

# 解析质量报告状态：PASS 可直接进入管理员批准；REVIEW_REQUIRED 需要指定领域审核；
# BLOCKED 被质量门禁拦截，必须重新解析、OCR 或修复源文件，不能靠普通备注放行。
ReviewReportStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCKED"]

# 结论严重级别：WARNING 仅提示；REVIEW_REQUIRED 需要人工/专业审核；BLOCKING 直接阻断发布。
ReviewFindingSeverity = Literal["WARNING", "REVIEW_REQUIRED", "BLOCKING"]

# 版本号是审核证据的一部分。解析或路由规则改变后必须递增，不能覆盖旧报告，
# 否则无法说明某个知识版本当时究竟使用了哪套门禁规则。
PARSER_PIPELINE_VERSION = "2026.08.13.1"
REVIEW_POLICY_VERSION = "fitness-knowledge-review-2026.08.13.1"

_COACH_REVIEW_DOCUMENT_TYPES = frozenset({"EXERCISE_SAFETY", "TRAINING_GUIDE"})
_CLINICAL_REVIEW_DOCUMENT_TYPES = frozenset({"MEDICAL_EXERCISE_GUIDELINE", "WEIGHT_MANAGEMENT"})
_REFERENCE_ONLY_DOCUMENT_TYPES = frozenset({"REFERENCE_PRESENTATION"})


@dataclass(frozen=True)
class KnowledgeReviewFinding:
    """审核报告中的一个稳定结论，包含机器可读编码和人工可读说明。"""

    code: str
    severity: ReviewFindingSeverity
    message: str
    pages: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "pages": list(self.pages),
        }


@dataclass(frozen=True)
class KnowledgeReviewReport:
    """针对一个上传任务和原始文件哈希生成的追加式审核证据。"""

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
    status: ReviewReportStatus
    quality_metrics: dict[str, Any]
    page_profiles: tuple[PdfPageProfile, ...]
    warnings: tuple[str, ...]
    findings: tuple[KnowledgeReviewFinding, ...]
    required_review_domains: tuple[str, ...]
    recommended_reviewer_roles: tuple[str, ...]
    required_qualifications: tuple[str, ...]
    created_at: datetime | None = None

    @property
    def is_current(self) -> bool:
        """报告是否仍匹配当前解析器和审核策略版本。"""
        current_parser_name, current_parser_version = _parser_identity(self.media_type)
        return (
            self.parser_name == current_parser_name
            and self.parser_version == current_parser_version
            and self.parser_pipeline_version == PARSER_PIPELINE_VERSION
            and self.review_policy_version == REVIEW_POLICY_VERSION
        )

    @property
    def can_admin_approve(self) -> bool:
        """无需专业审核的报告通过且未过期时，管理员可直接排队索引。"""

        return self.status == "PASS" and self.is_current


class KnowledgeReviewReportBuilder:
    """复用生产解析与父子切分规则，生成可持久化的上传前置审核报告。

    报告构建不调用 LLM，也不把文档类型当作医疗结论。它只做确定性质量检查并
    标记所需审核领域；教练和具备资质的专业人员后续必须通过独立审核决策留下
    页码、结论和身份凭证，才能解除相应限制。
    """

    def __init__(
        self,
        *,
        max_chunk_chars: int,
        overlap_chars: int,
        thresholds: DocumentQualityThresholds | None = None,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars
        self.thresholds = thresholds or DocumentQualityThresholds()

    def build(
        self,
        *,
        report_id: str,
        job_id: str,
        document_sha256: str,
        document_type: str,
        risk_level: str,
        requires_human_review: bool,
        parsed: ParsedDocument,
    ) -> KnowledgeReviewReport:
        """从本次真实解析结果生成版本化报告，不重新读取或修改原始文件。"""

        drafts = chunk_parsed_blocks(
            parsed.blocks,
            max_chunk_chars=self.max_chunk_chars,
            overlap_chars=self.overlap_chars,
        )
        total_pages = len(parsed.page_profiles) if parsed.media_type == "application/pdf" else None
        metrics = measure_document_quality(
            parsed.blocks,
            drafts,
            total_pages=total_pages,
            page_profiles=parsed.page_profiles,
        )
        findings = self._build_findings(
            document_type,
            risk_level,
            requires_human_review,
            parsed,
            metrics,
        )
        required_domains, roles, qualifications = _review_requirements(
            document_type,
            risk_level,
            requires_human_review,
            parsed.page_profiles,
        )
        if any(finding.severity == "BLOCKING" for finding in findings):
            status: ReviewReportStatus = "BLOCKED"
        elif required_domains:
            status = "REVIEW_REQUIRED"
        else:
            status = "PASS"
        parser_name, parser_version = _parser_identity(parsed.media_type)
        return KnowledgeReviewReport(
            id=report_id,
            job_id=job_id,
            report_version=1,
            document_sha256=document_sha256,
            parser_name=parser_name,
            parser_version=parser_version,
            parser_pipeline_version=PARSER_PIPELINE_VERSION,
            review_policy_version=REVIEW_POLICY_VERSION,
            media_type=parsed.media_type,
            declared_risk_level=risk_level,
            source_requires_human_review=requires_human_review,
            status=status,
            quality_metrics=metrics.as_dict(),
            page_profiles=parsed.page_profiles,
            warnings=parsed.warnings,
            findings=tuple(findings),
            required_review_domains=required_domains,
            recommended_reviewer_roles=roles,
            required_qualifications=qualifications,
        )

    def _build_findings(
        self,
        document_type: str,
        risk_level: str,
        requires_human_review: bool,
        parsed: ParsedDocument,
        metrics: DocumentQualityMetrics,
    ) -> list[KnowledgeReviewFinding]:
        """把阈值失败、页面路由和健身领域规则转换为稳定审核结论。"""

        findings: list[KnowledgeReviewFinding] = []
        quality_metrics = metrics.as_dict()
        for failure in self.thresholds.validate(metrics):
            metric_name = failure.split(maxsplit=1)[0]
            findings.append(
                KnowledgeReviewFinding(
                    code=f"QUALITY_{metric_name.upper()}",
                    severity="BLOCKING",
                    message=_quality_failure_message(metric_name, failure),
                    pages=_finding_pages(metric_name, quality_metrics),
                )
            )

        visual_pages = tuple(
            profile.page_number
            for profile in parsed.page_profiles
            if profile.route in {"VISUAL_REVIEW_REQUIRED", "OCR_AND_VISUAL_REVIEW_REQUIRED"}
        )
        if visual_pages:
            findings.append(
                KnowledgeReviewFinding(
                    code="FITNESS_VISUAL_REVIEW_REQUIRED",
                    severity="REVIEW_REQUIRED",
                    message="图片可能承载动作、姿态或风险信息，需要按页进行专业视觉审核。",
                    pages=visual_pages,
                )
            )
        if document_type in _COACH_REVIEW_DOCUMENT_TYPES:
            findings.append(
                KnowledgeReviewFinding(
                    code="FITNESS_COACH_REVIEW_REQUIRED",
                    severity="REVIEW_REQUIRED",
                    message="该资料涉及训练方法或运动安全，需要教练审核适用人群、动作和禁忌。",
                )
            )
        if document_type in _CLINICAL_REVIEW_DOCUMENT_TYPES:
            findings.append(
                KnowledgeReviewFinding(
                    code="CLINICAL_REVIEW_REQUIRED",
                    severity="REVIEW_REQUIRED",
                    message="该资料涉及疾病、营养或高风险运动建议，需要具备相应资质的专业人员审核。",
                )
            )
        if risk_level == "MEDICAL" and document_type not in _CLINICAL_REVIEW_DOCUMENT_TYPES:
            findings.append(
                KnowledgeReviewFinding(
                    code="DECLARED_MEDICAL_RISK_REVIEW_REQUIRED",
                    severity="REVIEW_REQUIRED",
                    message="来源清单将该资料标记为医疗风险，不能仅由普通管理员或教练批准。",
                )
            )
        if requires_human_review:
            findings.append(
                KnowledgeReviewFinding(
                    code="SOURCE_REQUIRES_HUMAN_REVIEW",
                    severity="REVIEW_REQUIRED",
                    message="可信来源清单明确要求人工复核，自动质量通过不能替代审核决定。",
                )
            )
        if document_type in _REFERENCE_ONLY_DOCUMENT_TYPES:
            findings.append(
                KnowledgeReviewFinding(
                    code="REFERENCE_ONLY_DOCUMENT_TYPE",
                    severity="BLOCKING",
                    message="该文档类型仅供人工参考，不允许进入生产检索索引。",
                )
            )
        return findings


def _review_requirements(
    document_type: str,
    risk_level: str,
    requires_human_review: bool,
    page_profiles: tuple[PdfPageProfile, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """返回审核领域、现有平台角色建议和尚需建立的资质要求。

    Java 平台当前有可验证的 ``COACH`` 角色，但没有医生、康复师或营养师资质模型。
    因此临床类要求只写入“资质要求”，不能伪装成一个已经可授权的平台角色。
    """

    domains: set[str] = set()
    roles: set[str] = set()
    qualifications: set[str] = set()
    has_visual_review = any(
        profile.route in {"VISUAL_REVIEW_REQUIRED", "OCR_AND_VISUAL_REVIEW_REQUIRED"}
        for profile in page_profiles
    )
    if document_type in _COACH_REVIEW_DOCUMENT_TYPES or has_visual_review:
        domains.add("FITNESS_COACHING_SAFETY")
        roles.add("COACH")
    if document_type in _CLINICAL_REVIEW_DOCUMENT_TYPES or risk_level == "MEDICAL":
        domains.add("CLINICAL_EXERCISE_SAFETY")
        qualifications.add("VERIFIED_HEALTH_PROFESSIONAL")
    if requires_human_review:
        domains.add("FITNESS_CONTENT_REVIEW")
        roles.add("COACH")
    return tuple(sorted(domains)), tuple(sorted(roles)), tuple(sorted(qualifications))


def _parser_identity(media_type: str) -> tuple[str, str]:
    """记录实际解析引擎版本；内部 Markdown 管线使用仓库版本号。"""

    package_by_media_type = {
        "application/pdf": ("pdfplumber", "pdfplumber"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
            "python-docx",
            "python-docx",
        ),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
            "openpyxl",
            "openpyxl",
        ),
    }
    descriptor = package_by_media_type.get(media_type)
    if descriptor is None:
        return "fitness-markdown-parser", PARSER_PIPELINE_VERSION
    parser_name, package_name = descriptor
    try:
        return parser_name, version(package_name)
    except PackageNotFoundError:
        # 依赖缺失通常会在真实解析时更早失败；这里仍保留稳定值，方便单元测试
        # 使用构造的 ParsedDocument 验证审核策略。
        return parser_name, "unknown"


def _quality_failure_message(metric_name: str, raw_failure: str) -> str:
    labels = {
        "noise_rate": "噪声率超过发布阈值",
        "fragment_rate": "短碎片率超过发布阈值",
        "duplicate_rate": "重复率超过发布阈值",
        "parent_integrity": "父节点完整性低于发布阈值",
        "table_integrity": "表格完整性低于发布阈值",
        "missing_pages": "存在未提取页面",
        "ocr_required_pages": "存在尚未完成 OCR 的页面",
    }
    return f"{labels.get(metric_name, '解析质量未达到发布阈值')}（{raw_failure}）"


def _finding_pages(metric_name: str, metrics: dict[str, Any]) -> tuple[int, ...]:
    if metric_name == "missing_pages":
        return tuple(int(page) for page in metrics.get("missing_pages", ()))
    if metric_name == "ocr_required_pages":
        return tuple(int(page) for page in metrics.get("ocr_required_pages", ()))
    return ()
