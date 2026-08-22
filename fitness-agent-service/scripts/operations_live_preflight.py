"""执行 Operations 真实联调前的安全前置检查。

前置检查不调用 DeepSeek，不调用业务指标接口，也不打印任何密钥。它只检查 Agent
和 Java Gateway 的存活/就绪探针，以及当前 Shell 是否提供认证服务签发的
AgentContext，帮助把“服务没启动”和“业务请求失败”区分开。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


class OperationsPreflightError(RuntimeError):
    """联调前置条件不满足。"""


@dataclass(frozen=True)
class ProbeResult:
    """一个不包含响应正文的探针结果。"""

    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    """构造前置检查命令参数。"""

    parser = argparse.ArgumentParser(description="Operations 真实联调前置检查")
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
        "--booking-url",
        default=None,
        help="可选预约写服务地址；传入后额外检查 fitness-booking-service 存活探针",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_PREFLIGHT_TIMEOUT_SECONDS", "5")),
        help="每个探针的超时时间，默认 5 秒",
    )
    return parser


def _value_is_ready(value: Any) -> bool:
    """识别 Agent readiness 响应中的布尔配置和字符串连接状态。"""

    return value is True or value == "ok"


def validate_agent_readiness(payload: Any) -> ProbeResult:
    """只根据脱敏 readiness 字段判断 Agent 是否允许接收业务流量。"""

    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        return ProbeResult("agent-ready", False, "响应不是合法 readiness 结构")
    checks = payload["checks"]
    failed = [name for name, value in checks.items() if not _value_is_ready(value)]
    if payload.get("status") != "ready" or failed:
        detail = "未就绪字段=" + ",".join(sorted(str(name) for name in failed))
        if not failed:
            detail = "状态非 ready"
        return ProbeResult(
            "agent-ready",
            False,
            detail,
        )
    return ProbeResult("agent-ready", True, "PostgreSQL、Redis、模型和 Gateway 配置已就绪")


def validate_live_response(name: str, payload: Any) -> ProbeResult:
    """校验存活探针的稳定响应，不输出完整响应正文。"""

    if isinstance(payload, dict) and payload.get("status") == "ok":
        return ProbeResult(name, True, "进程可响应")
    return ProbeResult(name, False, "存活探针返回状态异常")


async def _get_json(client: httpx.AsyncClient, name: str, url: str) -> ProbeResult:
    """访问一个探针，并把网络/JSON错误转换为稳定的诊断结果。"""

    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return ProbeResult(name, False, "无法连接服务")
    try:
        payload = response.json()
    except ValueError:
        return ProbeResult(name, False, f"HTTP {response.status_code} 且响应不是 JSON")
    if response.status_code >= 400 and name != "agent-ready":
        return ProbeResult(name, False, f"HTTP {response.status_code}")
    return (
        validate_agent_readiness(payload)
        if name == "agent-ready"
        else validate_live_response(name, payload)
    )


async def run_preflight(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    """执行所有前置检查并返回结果；不发起任何模型或业务查询。"""

    if args.timeout_seconds <= 0:
        raise OperationsPreflightError("--timeout-seconds 必须大于 0")
    context_result = ProbeResult(
        "admin-context",
        bool(os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()),
        "已提供签名上下文"
        if os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
        else "未提供签名上下文",
    )
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        results: list[ProbeResult] = [
            context_result,
            await _get_json(client, "agent-live", args.agent_url.rstrip("/") + "/health/live"),
            await _get_json(client, "agent-ready", args.agent_url.rstrip("/") + "/health/ready"),
            await _get_json(client, "gateway-live", args.gateway_url.rstrip("/") + "/health/live"),
        ]
        if args.booking_url:
            results.append(
                await _get_json(
                    client,
                    "booking-live",
                    args.booking_url.rstrip("/") + "/health/live",
                )
            )
    return tuple(results)


def main() -> int:
    """命令行入口：任何前置条件失败都返回非零状态。"""

    try:
        results = asyncio.run(run_preflight(build_parser().parse_args()))
    except OperationsPreflightError as exc:
        print(f"Operations 联调前置检查失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
