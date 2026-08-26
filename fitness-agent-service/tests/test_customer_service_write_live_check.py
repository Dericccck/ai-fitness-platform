from argparse import Namespace
from typing import Self
from unittest.mock import AsyncMock

import pytest

import scripts.customer_service_write_live_check as write_check
from scripts.customer_service_write_live_check import (
    BusinessLiveCheckError,
    CustomerServiceWriteLiveCheckError,
    TicketFact,
    WriteCheckConfig,
    _is_loopback_endpoint,
    _validate_ticket_fact,
    build_cleanup_sql,
    build_config,
    build_parser,
    validate_write_guard,
)


def _args(**changes: object) -> Namespace:
    values = {
        "endpoint": "http://127.0.0.1:8090",
        "timeout_seconds": 90.0,
        "poll_timeout_seconds": 60.0,
        "mysql_host": "127.0.0.1",
        "mysql_port": "3307",
        "mysql_database": "fitness",
        "mysql_username": "fitness",
        "mysql_password": "fitness_dev_2026",
        "mysql_container": "fitness-mysql",
        "execute": True,
    }
    values.update(changes)
    return Namespace(**values)


def test_write_check_requires_two_explicit_safety_switches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOMER_SERVICE_LIVE_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("CUSTOMER_SERVICE_LIVE_CLEANUP", raising=False)

    with pytest.raises(CustomerServiceWriteLiveCheckError, match="ALLOW_WRITE"):
        validate_write_guard(_args())

    monkeypatch.setenv("CUSTOMER_SERVICE_LIVE_ALLOW_WRITE", "1")
    with pytest.raises(CustomerServiceWriteLiveCheckError, match="CLEANUP"):
        validate_write_guard(_args())


def test_write_check_rejects_non_loopback_agent() -> None:
    assert _is_loopback_endpoint("http://127.0.0.1:8090")
    assert _is_loopback_endpoint("http://localhost:8090")
    assert not _is_loopback_endpoint("https://agent.example.com")
    assert not _is_loopback_endpoint("file:///tmp/agent")


def test_write_check_rejects_execute_without_cleanup_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOMER_SERVICE_LIVE_ALLOW_WRITE", "1")
    monkeypatch.setenv("CUSTOMER_SERVICE_LIVE_CLEANUP", "1")
    with pytest.raises(CustomerServiceWriteLiveCheckError, match="MySQL 凭证"):
        validate_write_guard(_args(mysql_username="", mysql_password=""))


def test_cleanup_sql_only_uses_exact_request_id() -> None:
    sql = build_cleanup_sql("customer-service-live-check-abc123")

    assert "START TRANSACTION" in sql
    assert "COMMIT" in sql
    assert "WHERE request_id = 'customer-service-live-check-abc123'" in sql
    assert "WHERE create_request_id = 'customer-service-live-check-abc123'" in sql
    assert "organization_id" not in sql
    assert "status" not in sql


def test_cleanup_sql_deletes_audit_before_consumption_and_ticket() -> None:
    sql = build_cleanup_sql("customer-service-live-check-abc123")

    assert sql.index("agent_customer_service_ticket_audit") < sql.index(
        "agent_customer_service_confirmation_consumption"
    )
    assert sql.index("agent_customer_service_confirmation_consumption") < sql.index(
        "agent_customer_service_ticket WHERE"
    )


def test_cleanup_sql_rejects_sql_fragments() -> None:
    with pytest.raises(CustomerServiceWriteLiveCheckError):
        build_cleanup_sql("fixture' OR 1=1")


def test_ticket_fact_requires_one_ticket_consumption_and_audit() -> None:
    fact = TicketFact(
        ticket_id="ticket-1",
        organization_id="org-1",
        subject_user_id="student-1",
        source="AGENT",
        status="OPEN",
        subject="[CUSTOMER_SERVICE_LIVE_FIXTURE_abc] 预约异常",
        description="中文描述",
        ticket_count=1,
        consumption_count=1,
        audit_count=1,
    )

    _validate_ticket_fact(fact, "[CUSTOMER_SERVICE_LIVE_FIXTURE_abc]")


def test_ticket_fact_rejects_incomplete_transaction() -> None:
    fact = TicketFact(
        ticket_id="ticket-1",
        organization_id="org-1",
        subject_user_id="student-1",
        source="AGENT",
        status="OPEN",
        subject="[CUSTOMER_SERVICE_LIVE_FIXTURE_abc] 预约异常",
        description="中文描述",
        ticket_count=1,
        consumption_count=1,
        audit_count=0,
    )

    with pytest.raises(CustomerServiceWriteLiveCheckError, match="幂等/事务闭环"):
        _validate_ticket_fact(fact, "[CUSTOMER_SERVICE_LIVE_FIXTURE_abc]")


def test_parser_does_not_enable_write_by_default() -> None:
    args = build_parser().parse_args([])
    assert args.execute is False


def test_build_config_requires_context_after_write_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOMER_SERVICE_LIVE_ALLOW_WRITE", "1")
    monkeypatch.setenv("CUSTOMER_SERVICE_LIVE_CLEANUP", "1")
    monkeypatch.delenv("AGENT_LIVE_AGENT_CONTEXT", raising=False)

    with pytest.raises(BusinessLiveCheckError, match="AGENT_LIVE_AGENT_CONTEXT"):
        build_config(_args())


def _write_config() -> WriteCheckConfig:
    return WriteCheckConfig(
        endpoint="http://127.0.0.1:8090",
        context="signed-context",
        timeout_seconds=5,
        poll_timeout_seconds=5,
        mysql_host="127.0.0.1",
        mysql_port="3307",
        mysql_database="fitness",
        mysql_username="fitness",
        mysql_password="fitness_dev_2026",
        mysql_container="fitness-mysql",
    )


class _FakeAsyncClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def _valid_ticket_fact() -> TicketFact:
    return TicketFact(
        ticket_id="ticket-1",
        organization_id="org-1",
        subject_user_id="student-1",
        source="AGENT",
        status="OPEN",
        subject="[CUSTOMER_SERVICE_LIVE_FIXTURE_abc] 预约异常",
        description="中文描述",
        ticket_count=1,
        consumption_count=1,
        audit_count=1,
    )


@pytest.mark.asyncio
async def test_run_check_cleans_exact_request_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功写入后的清理属于验收协议本身，不能依赖人工记得删除数据。"""

    cleaned_request_ids: list[str] = []
    confirmation_states = iter(
        [{"authorization_status": "PENDING"}, {"execution_status": "SUCCEEDED"}]
    )
    monkeypatch.setattr(write_check.httpx, "AsyncClient", lambda **_: _FakeAsyncClient())
    monkeypatch.setattr(write_check, "_ensure_request_id_is_new", lambda *_: None)
    monkeypatch.setattr(
        write_check, "_post_chat", AsyncMock(return_value={"route": "CUSTOMER_SERVICE"})
    )
    monkeypatch.setattr(
        write_check,
        "_validate_confirmation_response",
        lambda *_: ("confirmation-1", "创建客服工单"),
    )
    monkeypatch.setattr(
        write_check, "_get_confirmation", AsyncMock(side_effect=confirmation_states)
    )
    monkeypatch.setattr(
        write_check,
        "_decide_confirmation",
        AsyncMock(return_value={"authorization_status": "APPROVED"}),
    )
    monkeypatch.setattr(write_check, "_read_ticket_fact", lambda *_: _valid_ticket_fact())
    monkeypatch.setattr(write_check, "_validate_ticket_fact", lambda *_: None)
    monkeypatch.setattr(
        write_check, "_cleanup", lambda _config, request_id: cleaned_request_ids.append(request_id)
    )

    await write_check.run_check(_write_config())

    assert len(cleaned_request_ids) == 1
    assert cleaned_request_ids[0].startswith("customer-service-live-check-")


@pytest.mark.asyncio
async def test_run_check_cleans_exact_request_when_fact_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使写入后的事实校验失败，也必须执行 finally 清理，避免测试数据残留。"""

    cleaned_request_ids: list[str] = []
    confirmation_states = iter(
        [{"authorization_status": "PENDING"}, {"execution_status": "SUCCEEDED"}]
    )
    monkeypatch.setattr(write_check.httpx, "AsyncClient", lambda **_: _FakeAsyncClient())
    monkeypatch.setattr(write_check, "_ensure_request_id_is_new", lambda *_: None)
    monkeypatch.setattr(
        write_check, "_post_chat", AsyncMock(return_value={"route": "CUSTOMER_SERVICE"})
    )
    monkeypatch.setattr(
        write_check,
        "_validate_confirmation_response",
        lambda *_: ("confirmation-1", "创建客服工单"),
    )
    monkeypatch.setattr(
        write_check, "_get_confirmation", AsyncMock(side_effect=confirmation_states)
    )
    monkeypatch.setattr(
        write_check,
        "_decide_confirmation",
        AsyncMock(return_value={"authorization_status": "APPROVED"}),
    )
    monkeypatch.setattr(write_check, "_read_ticket_fact", lambda *_: _valid_ticket_fact())

    def reject_fact(*_: object) -> None:
        raise CustomerServiceWriteLiveCheckError("模拟事实校验失败")

    monkeypatch.setattr(write_check, "_validate_ticket_fact", reject_fact)
    monkeypatch.setattr(
        write_check, "_cleanup", lambda _config, request_id: cleaned_request_ids.append(request_id)
    )

    with pytest.raises(CustomerServiceWriteLiveCheckError, match="模拟事实校验失败"):
        await write_check.run_check(_write_config())

    assert len(cleaned_request_ids) == 1
    assert cleaned_request_ids[0].startswith("customer-service-live-check-")
