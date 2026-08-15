"""需要显式开启的 Operations 查询审计 PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.agent.operations_audit import OperationsAuditRepository
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_operations_audit_persists_success_and_failure_without_payloads() -> None:
    database = Database(Settings(_env_file=None))
    repository = OperationsAuditRepository(database)
    request_id = f"operations-audit-test-{uuid4()}"
    identity = AgentIdentity(
        subject=f"operations-audit-subject-{uuid4()}",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )
    try:
        await repository.record(
            identity=identity,
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            bucket="NONE",
            comparison_role="CURRENT",
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 15),
            row_count=4,
            status="SUCCEEDED",
            error_code=None,
            request_id=request_id,
            trace_id="trace-operations-audit-test",
        )
        await repository.record(
            identity=identity,
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            bucket="NONE",
            comparison_role="PREVIOUS_PERIOD",
            from_date=None,
            to_date=None,
            row_count=None,
            status="FAILED",
            error_code="GatewayClientError",
            request_id=request_id,
            trace_id="trace-operations-audit-test",
        )

        async with database.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT subject_user_id, actor_roles, metric, comparison_role, "
                            "row_count, status, error_code "
                            "FROM agent_operations_query_audits WHERE request_id = :request_id "
                            "ORDER BY comparison_role"
                        ),
                        {"request_id": request_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 2
        assert rows[0]["comparison_role"] == "CURRENT"
        assert rows[0]["status"] == "SUCCEEDED"
        assert rows[0]["row_count"] == 4
        assert rows[1]["comparison_role"] == "PREVIOUS_PERIOD"
        assert rows[1]["status"] == "FAILED"
        assert rows[1]["error_code"] == "GatewayClientError"
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_operations_query_audits WHERE request_id = :request_id"),
                {"request_id": request_id},
            )
        await database.close()
