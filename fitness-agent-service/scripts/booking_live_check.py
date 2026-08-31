"""执行预约 Agent 真实确认流程和可选写入联调。

默认只验证自然语言请求能进入预约路由并生成待确认单，不会创建预约。只有显式
使用 ``--execute`` 或设置 ``AGENT_LIVE_EXECUTE_WRITES=1``，才会通过正式确认 API
批准并执行真实预约写入；测试请求必须使用专门的本地测试数据。
"""

from __future__ import annotations

import argparse

from business_live_check_support import add_common_arguments, run_main


def _arguments() -> tuple[argparse.Namespace, str, str, str]:
    parser = argparse.ArgumentParser()
    add_common_arguments(parser, description="预约 Agent 真实联调")
    return (
        parser.parse_args(),
        "AGENT_BOOKING_LIVE_MESSAGE",
        "BOOKING",
        ("CREATE_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT"),
    )


if __name__ == "__main__":
    raise SystemExit(run_main(_arguments, label="预约"))
