from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agent.operations_audit import OperationsAuditRecord
from app.api.routes.admin_operations import router
from app.infrastructure.agent_context import AgentIdentity


class FakeVerifier:
    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    def verify(self, token: str) -> AgentIdentity:
        return AgentIdentity("admin-1", frozenset({"org-1", "org-2"}), self.roles, 1, 2)


class FakeConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext()


class FakeDatabase:
    engine = FakeEngine()


class FakeOperationsAuditRepository:
    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}

    async def list(
        self, connection: object, **kwargs: Any
    ) -> tuple[list[OperationsAuditRecord], bool]:
        self.filters = kwargs
        return [
            OperationsAuditRecord(
                id="audit-1",
                subject_user_id="admin-1",
                actor_roles="ADMIN",
                organization_id="org-1",
                metric="APPOINTMENT_COUNT",
                bucket="NONE",
                comparison_role="CURRENT",
                from_date=date(2026, 8, 1),
                to_date=date(2026, 8, 15),
                row_count=5,
                status="SUCCEEDED",
                error_code=None,
                request_id="request-1",
                trace_id="trace-1",
                created_at=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            )
        ], True


def build_app(roles: frozenset[str]) -> tuple[FastAPI, FakeOperationsAuditRepository]:
    app = FastAPI()
    app.state.context_verifier = FakeVerifier(roles)
    app.state.database = FakeDatabase()
    repository = FakeOperationsAuditRepository()
    app.state.operations_audit = repository
    app.include_router(router)
    return app, repository


async def test_platform_admin_can_query_operations_audits() -> None:
    app, repository = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
            params={
                "organization_id": "org-1",
                "metric": "APPOINTMENT_COUNT",
                "audit_status": "SUCCEEDED",
                "limit": "20",
                "offset": "10",
            },
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["metric"] == "APPOINTMENT_COUNT"
    assert response.json()["items"][0]["metric_definition"] == {
        "id": "APPOINTMENT_COUNT",
        "label": "预约总量",
        "description": "指定机构和时间范围内的预约记录总数。",
        "dimension_description": "总量维度，不返回预约明细。",
        "supported_buckets": ["DAY", "NONE", "WEEK"],
        "supports_previous_period": True,
        "supports_year_over_year": True,
    }
    assert response.json()["has_more"] is True
    assert "sql" not in response.json()["items"][0]
    assert "prompt" not in response.json()["items"][0]
    assert repository.filters["organization_id"] == "org-1"
    assert repository.filters["organization_ids"] is None
    assert repository.filters["status"] == "SUCCEEDED"
    assert repository.filters["offset"] == 10


async def test_admin_can_load_metric_catalog_without_querying_business_data() -> None:
    app, repository = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/metric-catalog",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 200
    assert response.json()["catalog_version"].startswith("sha256:")
    assert response.headers["cache-control"] == "private, max-age=300, must-revalidate"
    etag = response.headers["etag"]
    items = response.json()["items"]
    assert len(items) == 8
    completed = next(item for item in items if item["id"] == "COMPLETED_CLASS_COUNT")
    assert completed["label"] == "完课量"
    assert completed["supported_buckets"] == ["DAY", "NONE", "WEEK"]
    assert completed["supports_previous_period"] is True
    assert completed["supports_year_over_year"] is True
    new_customer = next(item for item in items if item["id"] == "NEW_CUSTOMER_COUNT")
    assert new_customer["label"] == "新客量"
    assert new_customer["supported_buckets"] == ["DAY", "NONE", "WEEK"]
    assert new_customer["supports_previous_period"] is True
    assert new_customer["supports_year_over_year"] is True
    revenue = next(item for item in items if item["id"] == "REVENUE_AMOUNT")
    assert revenue["label"] == "营收金额"
    assert revenue["supported_buckets"] == ["DAY", "NONE", "WEEK"]
    assert revenue["supports_previous_period"] is True
    assert revenue["supports_year_over_year"] is True
    course = next(item for item in items if item["id"] == "COURSE_APPOINTMENT_COUNT")
    assert course["label"] == "课程预约量"
    assert course["supported_buckets"] == ["DAY", "NONE", "WEEK"]
    assert course["supports_previous_period"] is True
    assert course["supports_year_over_year"] is True
    assert repository.filters == {}
    assert "organization_id" not in course
    assert "sql" not in course
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cached_response = await client.get(
            "/api/v1/admin/operations/metric-catalog",
            headers={
                "X-Agent-Context": "signed-context",
                "If-None-Match": etag,
            },
        )
    assert cached_response.status_code == 304
    assert cached_response.content == b""
    assert cached_response.headers["etag"] == etag
    assert repository.filters == {}


async def test_admin_audit_filters_reject_unsupported_metric_capability_combination() -> None:
    app, repository = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
            params={
                "metric": "APPOINTMENT_STATUS_BREAKDOWN",
                "bucket": "DAY",
            },
        )

    assert response.status_code == 422
    assert "does not support bucket DAY" in response.json()["detail"]
    assert repository.filters == {}


async def test_admin_audit_filters_reject_unsupported_comparison_combination() -> None:
    app, repository = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
            params={
                "metric": "REMAINING_CLASS_HOURS",
                "comparison_role": "PREVIOUS_PERIOD",
            },
        )

    assert response.status_code == 422
    assert "does not support PREVIOUS_PERIOD comparison" in response.json()["detail"]
    assert repository.filters == {}


async def test_admin_audit_filters_reject_unsupported_year_over_year_combination() -> None:
    app, repository = build_app(frozenset({"ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
            params={
                "metric": "REMAINING_CLASS_HOURS",
                "comparison_role": "SAME_PERIOD_LAST_YEAR",
            },
        )

    assert response.status_code == 422
    assert "does not support SAME_PERIOD_LAST_YEAR comparison" in response.json()["detail"]
    assert repository.filters == {}


async def test_organization_admin_is_restricted_to_signed_organizations() -> None:
    app, repository = build_app(frozenset({"ORGANIZATION_ADMIN"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
        )
        forbidden = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
            params={"organization_id": "org-9"},
        )

    assert allowed.status_code == 200
    assert repository.filters["organization_ids"] == ("org-1", "org-2")
    assert forbidden.status_code == 403


async def test_student_cannot_query_operations_audits() -> None:
    app, _ = build_app(frozenset({"STUDENT"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/query-audits",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 403


async def test_student_cannot_load_metric_catalog() -> None:
    app, _ = build_app(frozenset({"STUDENT"}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/operations/metric-catalog",
            headers={"X-Agent-Context": "signed-context"},
        )

    assert response.status_code == 403
