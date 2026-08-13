"""需要显式开启的 PostgreSQL 仓储契约测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database
from app.rag.admin_models import KnowledgeIngestionJob
from app.rag.admin_repository import KnowledgeIngestionRepository
from app.rag.formats import PdfPageProfile
from app.rag.review import KnowledgeReviewFinding, KnowledgeReviewReport
from app.rag.review_workflow import KnowledgeReviewDecision

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_job_and_review_report_are_committed_and_read_together() -> None:
    """验证事务写入、JSONB/数组恢复以及任务删除后的外键级联。"""

    database = Database(Settings(_env_file=None))
    repository = KnowledgeIngestionRepository(database)
    suffix = str(int(datetime.now(UTC).timestamp() * 1_000_000))
    job_id = f"postgres-review-smoke-{suffix}"
    report_id = f"postgres-review-report-{suffix}"
    now = datetime.now(UTC)
    job = KnowledgeIngestionJob(
        id=job_id,
        source_uri=f"knowledge://fitness/postgres-smoke-{suffix}.pdf",
        original_filename="smoke.pdf",
        storage_key=f"{job_id}.pdf",
        content_type="application/pdf",
        size_bytes=100,
        title="PostgreSQL 审核报告契约测试",
        document_type="TRAINING_GUIDE",
        organization_id=None,
        owner_user_id=None,
        visibility="GLOBAL",
        allowed_roles=("COACH",),
        effective_from=now,
        effective_to=None,
        requested_version=1,
        submitted_by="postgres-contract-test",
        status="PENDING_REVIEW",
        attempt_count=0,
        max_attempts=3,
        content_sha256="a" * 64,
    )
    report = KnowledgeReviewReport(
        id=report_id,
        job_id=job_id,
        report_version=1,
        document_sha256=job.content_sha256,
        parser_name="pdfplumber",
        parser_version="test",
        parser_pipeline_version="test-pipeline",
        review_policy_version="test-policy",
        media_type="application/pdf",
        declared_risk_level="CAUTION",
        source_requires_human_review=True,
        status="REVIEW_REQUIRED",
        quality_metrics={"noise_rate": 0.0, "missing_pages": []},
        page_profiles=(PdfPageProfile(1, 1, 0.7, 20, 0.05, 0, 1, "VISUAL_REVIEW_REQUIRED"),),
        warnings=("page 1 requires visual review",),
        findings=(
            KnowledgeReviewFinding(
                "FITNESS_VISUAL_REVIEW_REQUIRED",
                "REVIEW_REQUIRED",
                "需要教练按页审核。",
                (1,),
            ),
        ),
        required_review_domains=("FITNESS_COACHING_SAFETY",),
        recommended_reviewer_roles=("COACH",),
        required_qualifications=(),
    )
    try:
        created = await repository.create_job(job=job, review_report=report)
        restored = await repository.get_latest_review_report(created.id)

        assert restored.document_sha256 == job.content_sha256
        assert restored.quality_metrics["missing_pages"] == []
        assert restored.page_profiles[0].page_number == 1
        assert restored.findings[0].pages == (1,)
        assert restored.required_review_domains == ("FITNESS_COACHING_SAFETY",)

        decision = KnowledgeReviewDecision(
            id=f"postgres-decision-{suffix}",
            report_id=report.id,
            job_id=job.id,
            review_domain="FITNESS_COACHING_SAFETY",
            decision="APPROVED",
            scope_type="PAGES",
            page_numbers=(1,),
            regions=(),
            finding_codes=("FITNESS_VISUAL_REVIEW_REQUIRED",),
            reviewer_id="coach-contract-test",
            reviewer_roles=("COACH",),
            reviewer_capabilities=("KNOWLEDGE_REVIEW_FITNESS", "KNOWLEDGE_REVIEW_GLOBAL"),
            reviewer_qualifications=(),
            reviewer_organization_ids=(),
            comment="已核对测试页的动作信息和风险提示。",
        )
        outcome = await repository.record_review_decision(decision, restored)
        credential = await repository.get_publication_credential(job.id)

        assert outcome.publication_credential is not None
        assert credential is not None
        assert credential.decision_ids == (decision.id,)
        assert credential.approved_visual_pages == (1,)
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_ingestion_jobs WHERE id = :id"),
                {"id": job_id},
            )
        await database.close()
