from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.admin_knowledge import router
from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import KnowledgeAdminForbidden, KnowledgeIngestionJob
from app.rag.review import KnowledgeReviewFinding, KnowledgeReviewReport


def _job(status: str = "PENDING_REVIEW") -> KnowledgeIngestionJob:
    return KnowledgeIngestionJob(
        id="job-1",
        source_uri="knowledge://fitness/warmup.md",
        original_filename="warmup.md",
        storage_key="job-1.md",
        content_type="text/markdown",
        size_bytes=20,
        title="热身",
        document_type="FITNESS_GUIDE",
        organization_id=None,
        owner_user_id=None,
        visibility="GLOBAL",
        allowed_roles=(),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=None,
        requested_version=1,
        submitted_by="admin-1",
        status=status,  # type: ignore[arg-type]
        attempt_count=0,
        max_attempts=3,
    )


class FakeVerifier:
    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("user-1", frozenset({"org-1"}), self.roles, 1, 2)


class FakeSettings:
    rag_max_source_bytes = 1000


class FakeAdminService:
    def __init__(self) -> None:
        self.uploads: list[Any] = []

    async def submit_upload(self, identity: AgentIdentity, **kwargs: Any) -> KnowledgeIngestionJob:
        if not {"ADMIN", "ORG_ADMIN", "SUPER_ADMIN"}.intersection(identity.roles):
            raise KnowledgeAdminForbidden("administrator role is required")
        self.uploads.append(kwargs)
        return _job()

    async def get_review_report_status(
        self, identity: AgentIdentity, job_id: str
    ) -> tuple[KnowledgeReviewReport, bool]:
        if not {"ADMIN", "ORG_ADMIN", "SUPER_ADMIN"}.intersection(identity.roles):
            raise KnowledgeAdminForbidden("administrator role is required")
        report = KnowledgeReviewReport(
            id="report-1",
            job_id=job_id,
            report_version=1,
            document_sha256="a" * 64,
            parser_name="fitness-markdown-parser",
            parser_version="2026.08.13.1",
            parser_pipeline_version="2026.08.13.1",
            review_policy_version="fitness-knowledge-review-2026.08.13.1",
            media_type="text/markdown",
            declared_risk_level="NORMAL",
            source_requires_human_review=False,
            status="REVIEW_REQUIRED",
            quality_metrics={"noise_rate": 0.0},
            page_profiles=(),
            warnings=(),
            findings=(
                KnowledgeReviewFinding(
                    "FITNESS_COACH_REVIEW_REQUIRED",
                    "REVIEW_REQUIRED",
                    "需要教练审核。",
                ),
            ),
            required_review_domains=("FITNESS_COACHING_SAFETY",),
            recommended_reviewer_roles=("COACH",),
            required_qualifications=(),
        )
        return report, False


def build_app(roles: frozenset[str]) -> tuple[FastAPI, FakeAdminService]:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier(roles)
    app.state.settings = FakeSettings()
    admin = FakeAdminService()
    app.state.knowledge_admin = admin
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    return app, admin


async def test_student_cannot_upload_knowledge() -> None:
    app, _ = build_app(frozenset({"STUDENT"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/admin/knowledge/documents",
            headers={"X-Agent-Context": "signed-context"},
            data={
                "source_uri": "knowledge://fitness/warmup.md",
                "title": "热身",
                "document_type": "FITNESS_GUIDE",
                "effective_from": "2026-01-01T00:00:00Z",
            },
            files={"file": ("warmup.md", "# 热身".encode(), "text/markdown")},
        )

    assert response.status_code == 403


async def test_admin_upload_returns_pending_review_task() -> None:
    app, admin = build_app(frozenset({"ORG_ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/admin/knowledge/documents",
            headers={"X-Agent-Context": "signed-context"},
            data={
                "source_uri": "knowledge://fitness/warmup.md",
                "title": "热身",
                "document_type": "FITNESS_GUIDE",
                "visibility": "GLOBAL",
                "effective_from": "2026-01-01T00:00:00Z",
            },
            files={"file": ("warmup.md", "# 热身".encode(), "text/markdown")},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_REVIEW"
    assert admin.uploads[0]["file_name"] == "warmup.md"


async def test_admin_can_read_versioned_review_report_without_document_content() -> None:
    app, _ = build_app(frozenset({"ORG_ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/knowledge/jobs/job-1/review-report",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "REVIEW_REQUIRED"
    assert payload["can_admin_approve"] is False
    assert payload["required_review_domains"] == ["FITNESS_COACHING_SAFETY"]
    assert payload["recommended_reviewer_roles"] == ["COACH"]
    assert "content" not in payload
