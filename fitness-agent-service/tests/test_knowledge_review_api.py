from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.knowledge_review import router
from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import KnowledgeIngestionJob
from app.rag.review import KnowledgeReviewReport
from app.rag.review_service import KnowledgeReviewCase
from app.rag.review_workflow import (
    KnowledgePublicationCredential,
    KnowledgeReviewDecision,
    KnowledgeReviewOutcome,
    KnowledgeReviewRequirement,
)


class FakeVerifier:
    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity(
            subject="coach-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"COACH"}),
            issued_at=1,
            expires_at=2,
            capabilities=frozenset({"KNOWLEDGE_REVIEW_FITNESS", "KNOWLEDGE_REVIEW_GLOBAL"}),
        )


class FakeReviewService:
    def __init__(self) -> None:
        self.submitted: dict[str, object] | None = None

    async def get_case(self, identity: AgentIdentity, job_id: str) -> KnowledgeReviewCase:
        return KnowledgeReviewCase(
            job=_job(),
            report=_report(),
            requirements=(
                KnowledgeReviewRequirement(
                    "FITNESS_COACHING_SAFETY",
                    "PAGES",
                    (2, 4),
                    ("FITNESS_VISUAL_REVIEW_REQUIRED",),
                ),
            ),
            decisions=(),
            publication_credential=None,
        )

    async def submit_decision(
        self, identity: AgentIdentity, job_id: str, **kwargs: object
    ) -> KnowledgeReviewOutcome:
        self.submitted = kwargs
        decision = KnowledgeReviewDecision(
            id="decision-1",
            report_id="report-1",
            job_id=job_id,
            review_domain="FITNESS_COACHING_SAFETY",
            decision="APPROVED",
            scope_type="PAGES",
            page_numbers=(2, 4),
            regions=kwargs["regions"],  # type: ignore[arg-type]
            finding_codes=("FITNESS_VISUAL_REVIEW_REQUIRED",),
            reviewer_id=identity.subject,
            reviewer_roles=("COACH",),
            reviewer_capabilities=tuple(sorted(identity.capabilities)),
            reviewer_qualifications=(),
            reviewer_organization_ids=("org-1",),
            comment=str(kwargs["comment"]),
        )
        credential = KnowledgePublicationCredential(
            id="credential-1",
            job_id=job_id,
            report_id="report-1",
            report_version=1,
            document_sha256="a" * 64,
            parser_pipeline_version="2026.08.13.1",
            review_policy_version="fitness-knowledge-review-2026.08.13.1",
            decision_ids=(decision.id,),
            approved_visual_pages=(2, 4),
        )
        return KnowledgeReviewOutcome(decision, credential)


def _job() -> KnowledgeIngestionJob:
    return KnowledgeIngestionJob(
        id="job-1",
        source_uri="knowledge://fitness/squat.pdf",
        original_filename="squat.pdf",
        storage_key="job-1.pdf",
        content_type="application/pdf",
        size_bytes=100,
        title="深蹲图解",
        document_type="FITNESS_GUIDE",
        organization_id=None,
        owner_user_id=None,
        visibility="GLOBAL",
        allowed_roles=("COACH",),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=None,
        requested_version=1,
        submitted_by="admin-1",
        status="PENDING_REVIEW",
        attempt_count=0,
        max_attempts=3,
        content_sha256="a" * 64,
    )


def _report() -> KnowledgeReviewReport:
    return KnowledgeReviewReport(
        id="report-1",
        job_id="job-1",
        report_version=1,
        document_sha256="a" * 64,
        parser_name="pdfplumber",
        parser_version="test",
        parser_pipeline_version="2026.08.13.1",
        review_policy_version="fitness-knowledge-review-2026.08.13.1",
        media_type="application/pdf",
        declared_risk_level="CAUTION",
        source_requires_human_review=False,
        status="REVIEW_REQUIRED",
        quality_metrics={},
        page_profiles=(),
        warnings=(),
        findings=(),
        required_review_domains=("FITNESS_COACHING_SAFETY",),
        recommended_reviewer_roles=("COACH",),
        required_qualifications=(),
    )


def build_app() -> tuple[FastAPI, FakeReviewService]:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier()
    service = FakeReviewService()
    app.state.knowledge_review = service
    app.include_router(router)
    return app, service


async def test_review_case_exposes_required_pages_without_document_content() -> None:
    app, _ = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/knowledge-review/jobs/job-1",
            headers={"X-Agent-Context": "signed"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requirements"][0]["page_numbers"] == [2, 4]
    assert payload["document_sha256"] == "a" * 64
    assert "content" not in payload


async def test_review_decision_cannot_submit_roles_or_qualifications_in_body() -> None:
    app, service = build_app()
    payload = {
        "review_domain": "FITNESS_COACHING_SAFETY",
        "decision": "APPROVED",
        "scope_type": "PAGES",
        "page_numbers": [2, 4],
        "regions": [
            {
                "page_number": 2,
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
                "label": "膝关节轨迹",
            }
        ],
        "comment": "已逐页核对动作轨迹、适用人群和常见错误。",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/v1/knowledge-review/jobs/job-1/decisions",
            headers={"X-Agent-Context": "signed"},
            json={**payload, "reviewer_roles": ["SYSTEM_ADMIN"]},
        )
        accepted = await client.post(
            "/api/v1/knowledge-review/jobs/job-1/decisions",
            headers={"X-Agent-Context": "signed"},
            json=payload,
        )

    assert rejected.status_code == 422
    assert accepted.status_code == 201
    assert accepted.json()["publication_credential"]["approved_visual_pages"] == [2, 4]
    assert service.submitted is not None
