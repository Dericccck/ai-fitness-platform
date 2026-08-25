"""客服工单真实验收前的只读服务前置检查。

该脚本只检查 Agent、Gateway 和独立客服服务的存活/就绪探针，并确认当前 Shell 是否
提供 AgentContext。它不会调用 DeepSeek、客服工单查询接口或任何写接口，也不会输出
Token、用户资料和客服工单正文，目的是先区分“服务未启动”和“业务流程失败”。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


class CustomerServicePreflightError(RuntimeError):
    """客服工单真实验收的只读前置条件不满足。"""


@dataclass(frozen=True)
class ProbeResult:
    """一个不包含响应正文的探针结果。"""

    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    """构造客服验收前置检查参数。"""

    parser = argparse.ArgumentParser(description="客服工单真实验收前置检查")
    parser.add_argument(
        "--agent-url",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8081"),
        help="Java Gateway 地址，默认读取 AGENT_GATEWAY_BASE_URL",
    )
    parser.add_argument(
        "--customer-service-url",
        default=os.getenv("CUSTOMER_SERVICE_BASE_URL", "http://127.0.0.1:8084"),
        help="客服服务地址，默认读取 CUSTOMER_SERVICE_BASE_URL",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_PREFLIGHT_TIMEOUT_SECONDS", "5")),
        help="每个探针的超时时间，默认 5 秒",
    )
    return parser


def _value_is_ready(value: Any) -> bool:
    """识别 Agent readiness 中的布尔检查和字符串连接状态。"""

    return value is True or value == "ok"


def validate_agent_readiness(payload: Any) -> ProbeResult:
    """只使用脱敏 readiness 字段判断 Agent 是否允许接收真实联调流量。"""

    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        return ProbeResult("agent-ready", False, "响应不是合法 readiness 结构")
    failed = [
        name for name, value in payload["checks"].items() if not _value_is_ready(value)
    ]
    if payload.get("status") != "ready" or failed:
        detail = "未就绪字段=" + ",".join(sorted(str(name) for name in failed))
        if not failed:
            detail = "状态非 ready"
        return ProbeResult("agent-ready", False, detail)
    return ProbeResult("agent-ready", True, "Agent 依赖已就绪")


def validate_live_response(name: str, payload: Any) -> ProbeResult:
    """校验三个服务共用的稳定存活响应，不输出完整响应正文。"""

    if isinstance(payload, dict) and payload.get("status") == "ok":
        return ProbeResult(name, True, "进程可响应")
    return ProbeResult(name, False, "存活探针返回状态异常")


async def _get_json(client: httpx.AsyncClient, name: str, url: str) -> ProbeResult:
    """访问探针并收敛网络、HTTP 和 JSON 异常为安全诊断结果。"""

    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return ProbeResult(name, False, "无法连接服务")
    try:
        payload = response.json()
    except ValueError:
        return ProbeResult(name, False, f"HTTP {response.status_code} 且响应不是 JSON")
    if response.status_code >= 400:
        return ProbeResult(name, False, f"HTTP {response.status_code}")
    return (
        validate_agent_readiness(payload)
        if name == "agent-ready"
        else validate_live_response(name, payload)
    )


async def run_preflight(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    """执行只读服务检查，不发起模型请求或客服业务请求。"""

    if args.timeout_seconds <= 0:
        raise CustomerServicePreflightError("--timeout-seconds 必须大于 0")
    signed_context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    context_result = ProbeResult(
        "agent-context",
        bool(signed_context),
        "已提供签名上下文" if signed_context else "未提供签名上下文",
    )
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        results = [
            context_result,
            await _get_json(client, "agent-live", args.agent_url.rstrip("/") + "/health/live"),
            await _get_json(client, "agent-ready", args.agent_url.rstrip("/") + "/health/ready"),
            await _get_json(client, "gateway-live", args.gateway_url.rstrip("/") + "/health/live"),
            await _get_json(
                client,
                "customer-service-live",
                args.customer_service_url.rstrip("/") + "/health/live",
            ),
        ]
    return tuple(results)


def main() -> int:
    """命令行入口：任一前置条件失败都返回非零状态。"""

    try:
        results = asyncio.run(run_preflight(build_parser().parse_args()))
    except CustomerServicePreflightError as exc:
        print(f"客服工单联调前置检查失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
