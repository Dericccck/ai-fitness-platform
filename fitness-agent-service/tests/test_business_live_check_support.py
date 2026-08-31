"""预约/健身真实联调脚本的安全边界测试。"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from business_live_check_support import (
    BusinessLiveCheckError,
    _validate_confirmation_response,
    build_config,
    run_business_live_check,
)


def args(*, message: str = "创建测试预约", execute: bool = False) -> Namespace:
    """构造不依赖真实环境的命令行参数。"""

    return Namespace(
        endpoint="http://127.0.0.1:8090",
        message=message,
        conversation_id=None,
        timeout_seconds=90.0,
        poll_timeout_seconds=60.0,
        execute=execute,
        keep_confirmation=False,
    )


def test_build_config_requires_business_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有明确测试请求时必须拒绝，避免脚本猜测业务参数。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    monkeypatch.delenv("AGENT_BOOKING_LIVE_MESSAGE", raising=False)

    with pytest.raises(BusinessLiveCheckError, match="AGENT_BOOKING_LIVE_MESSAGE"):
        build_config(
            args(message=""),
            message_env="AGENT_BOOKING_LIVE_MESSAGE",
            route="BOOKING",
            action_prefix=("CREATE_APPOINTMENT",),
        )


def test_confirmation_summary_must_match_booking_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使路由正确，也不能把其他写操作当成预约联调成功。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    config = build_config(
        args(),
        message_env="AGENT_BOOKING_LIVE_MESSAGE",
        route="BOOKING",
        action_prefix=("CREATE_APPOINTMENT",),
    )
    payload = {
        "route": "BOOKING",
        "status": "CONFIRMATION_REQUIRED",
        "confirmation_id": "confirmation-1",
        "confirmation_summary": {"action": "CREATE_TRAINING_DRAFT", "operation": "错误操作"},
    }

    with pytest.raises(BusinessLiveCheckError, match="确认单动作不属于"):
        _validate_confirmation_response(payload, config)


def test_fitness_confirmation_requires_real_confirmation_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """训练草案必须停在正式确认边界，不能把普通回答当作写入成功。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    config = build_config(
        args(message="生成测试训练计划"),
        message_env="AGENT_FITNESS_LIVE_MESSAGE",
        route="FITNESS_COACHING",
        action_prefix=("CREATE_TRAINING_DRAFT",),
    )
    payload = {
        "route": "FITNESS_COACHING",
        "status": "COMPLETED",
        "confirmation_id": None,
        "confirmation_summary": None,
    }

    with pytest.raises(BusinessLiveCheckError, match="CONFIRMATION_REQUIRED"):
        _validate_confirmation_response(payload, config)


def test_execute_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认不执行真实写入，环境变量也必须显式设置为 1 才开启。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    config = build_config(
        args(),
        message_env="AGENT_BOOKING_LIVE_MESSAGE",
        route="BOOKING",
        action_prefix=("CREATE_APPOINTMENT",),
    )
    assert config.execute is False

    monkeypatch.setenv("AGENT_LIVE_EXECUTE_WRITES", "1")
    config = build_config(
        args(execute=True),
        message_env="AGENT_BOOKING_LIVE_MESSAGE",
        route="BOOKING",
        action_prefix=("CREATE_APPOINTMENT",),
    )
    assert config.execute is True


class FakeLiveHttpClient:
    """给联调脚本提供可控的 HTTP 边界，不启动 Agent 或写入真实业务库。"""

    instances: ClassVar[list[Self]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.confirmation_get_count = 0
        self.__class__.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> Any:
        self.calls.append(("POST", url, {"headers": headers, "json": json}))
        if url.endswith("/chat"):
            return _FakeResponse(
                200,
                {
                    "route": "BOOKING",
                    "status": "CONFIRMATION_REQUIRED",
                    "confirmation_id": "confirmation-live-1",
                    "confirmation_summary": {
                        "action": "CREATE_APPOINTMENT",
                        "operation": "创建预约",
                    },
                },
            )
        if json["decision"] == "REJECT":
            return _FakeResponse(200, {"authorization_status": "REJECTED"})
        return _FakeResponse(200, {"authorization_status": "APPROVED"})

    async def get(self, url: str, *, headers: dict[str, str]) -> Any:
        self.calls.append(("GET", url, {"headers": headers}))
        self.confirmation_get_count += 1
        if self.confirmation_get_count == 1:
            return _FakeResponse(200, {"authorization_status": "PENDING"})
        return _FakeResponse(
            200,
            {"authorization_status": "APPROVED", "execution_status": "SUCCEEDED"},
        )


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_dry_run_rejects_confirmation_and_does_not_leave_pending_record(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """默认联调必须自动拒绝确认单，避免只测到 PENDING 就留下脏数据。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context-not-for-logs")
    monkeypatch.setattr("business_live_check_support.httpx.AsyncClient", FakeLiveHttpClient)
    FakeLiveHttpClient.instances.clear()
    config = build_config(
        args(),
        message_env="AGENT_BOOKING_LIVE_MESSAGE",
        route="BOOKING",
        action_prefix=("CREATE_APPOINTMENT",),
    )

    await run_business_live_check(config, label="预约")

    client = FakeLiveHttpClient.instances[-1]
    decisions = [call for call in client.calls if call[0] == "POST" and "decisions" in call[1]]
    assert decisions[0][2]["json"] == {
        "decision": "REJECT",
        "decision_request_id": decisions[0][2]["json"]["decision_request_id"],
    }
    assert decisions[0][2]["headers"]["X-Agent-Context"] == "signed-context-not-for-logs"
    assert "signed-context-not-for-logs" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_execute_polls_until_confirmation_execution_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式执行时必须先批准，再读取最终 SUCCEEDED，而不是把 APPROVED 当成业务成功。"""

    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    monkeypatch.setattr("business_live_check_support.httpx.AsyncClient", FakeLiveHttpClient)
    FakeLiveHttpClient.instances.clear()
    config = build_config(
        args(execute=True),
        message_env="AGENT_BOOKING_LIVE_MESSAGE",
        route="BOOKING",
        action_prefix=("CREATE_APPOINTMENT",),
    )

    await run_business_live_check(config, label="预约")

    client = FakeLiveHttpClient.instances[-1]
    decisions = [call for call in client.calls if call[0] == "POST" and "decisions" in call[1]]
    assert decisions[0][2]["json"]["decision"] == "APPROVE"
    assert len([call for call in client.calls if call[0] == "GET"]) == 2
