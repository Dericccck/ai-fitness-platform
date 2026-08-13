from dataclasses import replace

from app.rag.formats import ParsedBlock, ParsedDocument, PdfPageProfile
from app.rag.review import KnowledgeReviewReportBuilder


def _builder() -> KnowledgeReviewReportBuilder:
    return KnowledgeReviewReportBuilder(max_chunk_chars=1200, overlap_chars=120)


def test_visual_page_requires_coach_review_but_ocr_page_is_blocked() -> None:
    parsed = ParsedDocument(
        blocks=(ParsedBlock(kind="TEXT", content="深蹲动作说明。", source_page=2),),
        media_type="application/pdf",
        page_profiles=(
            PdfPageProfile(1, 1, 1.0, 0, 0.0, 0, 0, "OCR_REQUIRED"),
            PdfPageProfile(2, 1, 0.7, 20, 0.05, 0, 1, "VISUAL_REVIEW_REQUIRED"),
        ),
    )

    report = _builder().build(
        report_id="report-1",
        job_id="job-1",
        document_sha256="a" * 64,
        document_type="TRAINING_GUIDE",
        risk_level="NORMAL",
        requires_human_review=False,
        parsed=parsed,
    )

    assert report.status == "BLOCKED"
    assert report.required_review_domains == ("FITNESS_COACHING_SAFETY",)
    assert report.recommended_reviewer_roles == ("COACH",)
    assert {finding.code for finding in report.findings} >= {
        "QUALITY_MISSING_PAGES",
        "QUALITY_OCR_REQUIRED_PAGES",
        "FITNESS_VISUAL_REVIEW_REQUIRED",
    }


def test_reference_only_type_cannot_be_approved_even_when_text_quality_passes() -> None:
    parsed = ParsedDocument(
        blocks=(ParsedBlock(kind="TEXT", content="参考演示资料。"),),
        media_type="text/markdown",
    )

    report = _builder().build(
        report_id="report-2",
        job_id="job-2",
        document_sha256="b" * 64,
        document_type="REFERENCE_PRESENTATION",
        risk_level="NORMAL",
        requires_human_review=False,
        parsed=parsed,
    )

    assert report.status == "BLOCKED"
    assert any(f.code == "REFERENCE_ONLY_DOCUMENT_TYPE" for f in report.findings)


def test_pass_report_becomes_unapprovable_after_pipeline_version_changes() -> None:
    parsed = ParsedDocument(
        blocks=(ParsedBlock(kind="TEXT", content="普通健身知识。"),),
        media_type="text/markdown",
    )
    report = _builder().build(
        report_id="report-3",
        job_id="job-3",
        document_sha256="c" * 64,
        document_type="FITNESS_GUIDE",
        risk_level="NORMAL",
        requires_human_review=False,
        parsed=parsed,
    )

    assert report.status == "PASS"
    assert report.can_admin_approve is True
    assert replace(report, parser_pipeline_version="obsolete").can_admin_approve is False
