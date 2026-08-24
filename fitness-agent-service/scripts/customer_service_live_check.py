"""执行客服工单确认流程的无写入真实联调。

该脚本调用已经启动的 Agent API，验证明确的“提交客服工单”请求能进入
``CUSTOMER_SERVICE`` 路由并生成正确的 ``CREATE_CUSTOMER_SERVICE_TICKET`` 确认单。
默认会自动拒绝确认单，因此不会创建真实工单；当前客服服务还没有安全的测试工单删除
接口，脚本刻意不提供 ``--execute``，避免验收数据无法清理。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:
    from business_live_check_support import (
        BusinessLiveCheckError,
        LiveCheckConfig,
        require_context,
        run_business_live_check,
    )
except ModuleNotFoundError:  # 从项目根目录运行 pytest 时使用包路径。
    from scripts.business_live_check_support import (
        BusinessLiveCheckError,
        LiveCheckConfig,
        require_context,
        run_business_live_check,
    )

DEFAULT_MESSAGE = (
    "请帮我提交客服工单，反馈预约状态异常：我昨天预约了私教课，页面显示状态不一致，请客服核查。"
)


def build_parser() -> argparse.ArgumentParser:
    """构造无写入联调参数；不暴露 execute 开关是有意的安全设计。"""

    parser = argparse.ArgumentParser(description="客服工单确认流程无写入联调")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--message",
        default=os.getenv("AGENT_CUSTOMER_SERVICE_LIVE_MESSAGE", DEFAULT_MESSAGE),
        help="明确要求提交客服工单的测试请求",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="单次 HTTP 请求超时时间，默认 90 秒",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "60")),
        help="确认单状态轮询超时时间，默认 60 秒",
    )
    parser.add_argument(
        "--keep-confirmation",
        action="store_true",
        default=os.getenv("AGENT_LIVE_KEEP_CONFIRMATION") == "1",
        help="保留 PENDING 确认单供人工检查；默认自动 REJECT 清理",
    )
    return parser


def build_config(args: argparse.Namespace) -> LiveCheckConfig:
    """构造固定为 execute=False 的联调配置。"""

    message = str(args.message).strip()
    if not message:
        raise BusinessLiveCheckError("客服工单测试请求不能为空")
    if args.timeout_seconds <= 0 or args.poll_timeout_seconds <= 0:
        raise BusinessLiveCheckError("timeout 参数必须大于 0")
    return LiveCheckConfig(
        endpoint=str(args.endpoint).rstrip("/"),
        context=require_context(),
        message=message,
        expected_route="CUSTOMER_SERVICE",
        expected_action_prefix=("CREATE_CUSTOMER_SERVICE_TICKET",),
        timeout_seconds=float(args.timeout_seconds),
        poll_interval_seconds=min(1.0, max(0.1, float(args.poll_timeout_seconds) / 20)),
        poll_timeout_seconds=float(args.poll_timeout_seconds),
        execute=False,
        keep_confirmation=bool(args.keep_confirmation),
    )


def main() -> int:
    """命令行入口：确认流程或自动拒绝清理失败时返回非零状态。"""

    try:
        config = build_config(build_parser().parse_args())
        asyncio.run(run_business_live_check(config, label="Customer Service"))
    except (BusinessLiveCheckError, ValueError) as exc:
        print(f"Customer Service 确认联调失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
