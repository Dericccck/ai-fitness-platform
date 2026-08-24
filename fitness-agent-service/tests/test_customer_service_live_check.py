from argparse import Namespace

import pytest

from scripts.customer_service_live_check import build_config, build_parser


def test_customer_service_live_check_is_write_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LIVE_AGENT_CONTEXT", "signed-context")
    config = build_config(
        Namespace(
            endpoint="http://127.0.0.1:8090",
            message="请提交客服工单，反馈预约状态异常",
            timeout_seconds=90,
            poll_timeout_seconds=60,
            keep_confirmation=False,
        )
    )

    assert config.expected_route == "CUSTOMER_SERVICE"
    assert config.expected_action_prefix == ("CREATE_CUSTOMER_SERVICE_TICKET",)
    assert config.execute is False


def test_customer_service_live_check_does_not_accept_execute_flag() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--execute"])
