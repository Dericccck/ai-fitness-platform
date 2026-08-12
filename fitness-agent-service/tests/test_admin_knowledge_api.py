from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.admin_knowledge import router
from app.infrastructure.agent_context import AgentIdentity
from app.rag.admin_models import KnowledgeAdminForbidden, KnowledgeIngestionJob


def _job(status: str = "PENDING_REVIEW") -> KnowledgeIngestionJob:
    return KnowledgeIngestionJob(
        id="job-1",
        source_uri="knowledge://fitness/warmup.md",
        original_filename="warmup.md",
        storage_key="job-1.md",
        content_type="text/markdown",
        size_bytes=20,
        title="Warmup",
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
                "title": "Warmup",
                "document_type": "FITNESS_GUIDE",
                "effective_from": "2026-01-01T00:00:00Z",
            },
            files={"file": ("warmup.md", b"# Warmup", "text/markdown")},
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
                "title": "Warmup",
                "document_type": "FITNESS_GUIDE",
                "visibility": "GLOBAL",
                "effective_from": "2026-01-01T00:00:00Z",
            },
            files={"file": ("warmup.md", b"# Warmup", "text/markdown")},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_REVIEW"
    assert admin.uploads[0]["file_name"] == "warmup.md"
