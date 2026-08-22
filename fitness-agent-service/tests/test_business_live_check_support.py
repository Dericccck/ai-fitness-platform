"""Booking/Fitness 真实联调脚本的安全边界测试。"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from business_live_check_support import (
    BusinessLiveCheckError,
    _validate_confirmation_response,
    build_config,
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
