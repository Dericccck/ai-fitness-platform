from datetime import date

import pytest

from app.agent.operations_audit import (
    OperationsAuditRepository,
    OperationsAuditValidationError,
)
from app.infrastructure.agent_context import AgentIdentity


class FakeConnection:
    def __init__(self) -> None:
        self.statement = None
        self.params: dict[str, object] | None = None

    async def execute(self, statement: object, params: dict[str, object]) -> None:
        self.statement = statement
        self.params = params


class FakeBegin:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> FakeBegin:
        return FakeBegin(self.connection)


class FakeDatabase:
    def __init__(self, connection: FakeConnection) -> None:
        self.engine = FakeEngine(connection)


@pytest.mark.asyncio
async def test_operations_audit_repository_only_persists_minimal_metadata() -> None:
    connection = FakeConnection()
    repository = OperationsAuditRepository(FakeDatabase(connection))  # type: ignore[arg-type]
    identity = AgentIdentity(
        subject="admin-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"ORGANIZATION_ADMIN"}),
        issued_at=1,
        expires_at=2,
    )

    await repository.record(
        identity=identity,
        organization_id="org-1",
        metric="APPOINTMENT_COUNT",
        bucket="NONE",
        comparison_role="CURRENT",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 15),
        row_count=3,
        status="SUCCEEDED",
        error_code=None,
        request_id="request-1",
        trace_id="trace-1",
    )

    assert connection.params is not None
    assert connection.params["subject_user_id"] == "admin-1"
    assert connection.params["actor_roles"] == "ORGANIZATION_ADMIN"
    assert connection.params["row_count"] == 3
    assert connection.params["status"] == "SUCCEEDED"
    assert "sql" not in connection.params
    assert "prompt" not in connection.params
    assert "rows" not in connection.params


def test_operations_audit_repository_rejects_unknown_metric() -> None:
    connection = FakeConnection()
    repository = OperationsAuditRepository(FakeDatabase(connection))  # type: ignore[arg-type]

    with pytest.raises(OperationsAuditValidationError):
        # 该调用在打开数据库事务前失败，避免错误事件绕过固定指标目录。
        import asyncio

        asyncio.run(
            repository.record(
                identity=None,
                organization_id="org-1",
                metric="ARBITRARY_SQL",
                bucket="NONE",
                comparison_role="CURRENT",
                from_date=None,
                to_date=None,
                row_count=None,
                status="FAILED",
                error_code="GatewayClientError",
                request_id=None,
                trace_id=None,
            )
        )
