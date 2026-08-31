from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import KnowledgeAdminForbidden, KnowledgeIngestionJob
from app.rag.formats import PdfPageProfile
from app.rag.review import PARSER_PIPELINE_VERSION, KnowledgeReviewFinding, KnowledgeReviewReport
from app.rag.review_workflow import (
    CLINICAL_REVIEW_CAPABILITY,
    FITNESS_REVIEW_CAPABILITY,
    GLOBAL_REVIEW_CAPABILITY,
    HEALTH_PROFESSIONAL_QUALIFICATION,
    KnowledgePublicationCredential,
    KnowledgeReviewRegion,
    review_requirements,
    validate_decision_scope,
    validate_reviewer,
)


def identity(
    subject: str,
    *,
    roles: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
    qualifications: tuple[str, ...] = (),
    organizations: tuple[str, ...] = ("org-1",),
) -> AgentIdentity:
    return AgentIdentity(
        subject,
        frozenset(organizations),
        frozenset(roles),
        1,
        2,
        frozenset(capabilities),
        frozenset(qualifications),
    )


def job(**overrides: object) -> KnowledgeIngestionJob:
    values: dict[str, object] = {
        "id": "job-1",
        "source_uri": "knowledge://fitness/squat.pdf",
        "original_filename": "squat.pdf",
        "storage_key": "job-1.pdf",
        "content_type": "application/pdf",
        "size_bytes": 100,
        "title": "深蹲动作指南",
        "document_type": "EXERCISE_SAFETY",
        "organization_id": None,
        "owner_user_id": None,
        "visibility": "GLOBAL",
        "allowed_roles": ("COACH",),
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "effective_to": None,
        "requested_version": 1,
        "submitted_by": "uploader-1",
        "status": "PENDING_REVIEW",
        "attempt_count": 0,
        "max_attempts": 3,
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return KnowledgeIngestionJob(**values)  # type: ignore[arg-type]


def report(*, document_level: bool = False, clinical: bool = False) -> KnowledgeReviewReport:
    findings = [
        KnowledgeReviewFinding(
            "FITNESS_VISUAL_REVIEW_REQUIRED",
            "REVIEW_REQUIRED",
            "动作图片需要按页审核。",
            (2, 4),
        )
    ]
    domains = ["FITNESS_COACHING_SAFETY"]
    if document_level:
        findings.append(
            KnowledgeReviewFinding(
                "FITNESS_COACH_REVIEW_REQUIRED",
                "REVIEW_REQUIRED",
                "训练指南需要通读。",
            )
        )
    if clinical:
        findings.append(
            KnowledgeReviewFinding(
                "CLINICAL_REVIEW_REQUIRED",
                "REVIEW_REQUIRED",
                "医疗运动建议需要专业审核。",
            )
        )
        domains.append("CLINICAL_EXERCISE_SAFETY")
    return KnowledgeReviewReport(
        id="report-1",
        job_id="job-1",
        report_version=1,
        document_sha256="a" * 64,
        parser_name="pdfplumber",
        parser_version="test",
        parser_pipeline_version=PARSER_PIPELINE_VERSION,
        review_policy_version="fitness-knowledge-review-2026.08.13.1",
        media_type="application/pdf",
        declared_risk_level="MEDICAL" if clinical else "CAUTION",
        source_requires_human_review=False,
        status="REVIEW_REQUIRED",
        quality_metrics={},
        page_profiles=tuple(
            PdfPageProfile(
                number,
                1 if number in {2, 4} else 0,
                0.7 if number in {2, 4} else 0,
                30,
                0.05,
                0,
                1,
                "VISUAL_REVIEW_REQUIRED" if number in {2, 4} else "NORMAL",
            )
            for number in range(1, 6)
        ),
        warnings=(),
        findings=tuple(findings),
        required_review_domains=tuple(sorted(domains)),
        recommended_reviewer_roles=("COACH",),
        required_qualifications=((HEALTH_PROFESSIONAL_QUALIFICATION,) if clinical else ()),
    )


def test_visual_only_requires_exact_pages_but_training_guide_requires_document() -> None:
    visual = review_requirements(report())[0]
    document = review_requirements(report(document_level=True))[0]

    assert visual.scope_type == "PAGES"
    assert visual.page_numbers == (2, 4)
    assert document.scope_type == "DOCUMENT"
    assert document.page_numbers == ()

    validate_decision_scope(
        visual,
        "PAGES",
        (2, 4),
        (KnowledgeReviewRegion(2, 0.1, 0.2, 0.3, 0.4, "膝关节轨迹"),),
        total_pages=5,
    )
    with pytest.raises(ValueError, match="完全覆盖"):
        validate_decision_scope(visual, "PAGES", (2,), (), total_pages=5)
    with pytest.raises(ValueError, match="归一化坐标"):
        validate_decision_scope(
            visual,
            "PAGES",
            (2, 4),
            (KnowledgeReviewRegion(2, 0.9, 0.2, 0.3, 0.4),),
            total_pages=5,
        )


def test_reviewer_uses_signed_capabilities_and_cannot_self_review() -> None:
    requirement = review_requirements(report())[0]
    authorized = identity(
        "coach-1",
        roles=("COACH",),
        capabilities=(FITNESS_REVIEW_CAPABILITY, GLOBAL_REVIEW_CAPABILITY),
    )
    validate_reviewer(authorized, job(), requirement)

    with pytest.raises(KnowledgeAdminForbidden, match="上传者"):
        validate_reviewer(replace(authorized, subject="uploader-1"), job(), requirement)
    with pytest.raises(KnowledgeAdminForbidden, match="健身知识审查"):
        validate_reviewer(
            identity("coach-2", roles=("COACH",), capabilities=(GLOBAL_REVIEW_CAPABILITY,)),
            job(),
            requirement,
        )


def test_clinical_review_requires_both_capability_and_verified_qualification() -> None:
    clinical = next(
        item
        for item in review_requirements(report(clinical=True))
        if item.domain == "CLINICAL_EXERCISE_SAFETY"
    )
    with pytest.raises(KnowledgeAdminForbidden, match="健康专业资质"):
        validate_reviewer(
            identity(
                "doctor-1",
                capabilities=(CLINICAL_REVIEW_CAPABILITY, GLOBAL_REVIEW_CAPABILITY),
            ),
            job(),
            clinical,
        )
    validate_reviewer(
        identity(
            "doctor-1",
            capabilities=(CLINICAL_REVIEW_CAPABILITY, GLOBAL_REVIEW_CAPABILITY),
            qualifications=(HEALTH_PROFESSIONAL_QUALIFICATION,),
        ),
        job(),
        clinical,
    )


def test_publication_credential_is_bound_to_hash_report_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_report = report()
    monkeypatch.setattr(
        "app.rag.review._parser_identity", lambda media_type: ("pdfplumber", "test")
    )
    credential = KnowledgePublicationCredential(
        id="credential-1",
        job_id="job-1",
        report_id="report-1",
        report_version=1,
        document_sha256="a" * 64,
        parser_pipeline_version=PARSER_PIPELINE_VERSION,
        review_policy_version="fitness-knowledge-review-2026.08.13.1",
        decision_ids=("decision-1",),
        approved_visual_pages=(2, 4),
    )

    assert credential.validates(current_report, job()) is True
    assert credential.validates(current_report, job(content_sha256="b" * 64)) is False
