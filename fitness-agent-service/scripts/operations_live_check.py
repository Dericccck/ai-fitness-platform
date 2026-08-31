"""执行一次真实经营 Agent 冒烟联调。

该脚本只调用已经启动的 Agent HTTP API，不创建假身份、不直接连接 MySQL，也不绕过
Java Gateway。调用者必须显式提供认证服务签发的组织管理员 AgentContext；脚本不会把
Token 写入输出，便于在本地或预发布环境重复验证 DeepSeek、PostgreSQL/Redis、Agent
Runtime、Java Gateway 和业务数据库是否真正连通。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

import httpx

DEFAULT_MESSAGE = "统计 2026-08-01 至 2026-08-15 的营收金额按周趋势"


class OperationsLiveCheckError(RuntimeError):
    """真实联调未达到预期，不暴露响应正文或签名上下文。"""


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数，敏感 AgentContext 只从环境变量读取。"""

    parser = argparse.ArgumentParser(description="真实经营 Agent HTTP 冒烟联调")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--message",
        default=os.getenv("AGENT_LIVE_MESSAGE", DEFAULT_MESSAGE),
        help="精确的经营问题，默认验证营收金额按周趋势",
    )
    parser.add_argument(
        "--conversation-id",
        default=None,
        help="可选会话 ID；不传时自动生成一次性 ID，避免污染已有会话",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="HTTP 请求超时时间，默认 90 秒",
    )
    return parser


def _require_context() -> str:
    """读取调用者提供的签名上下文，拒绝空值但不打印 Token。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise OperationsLiveCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请使用认证服务签发的组织管理员 Token"
        )
    return context


def _response_summary(payload: Any) -> tuple[str, str, int, str]:
    """校验 Agent 稳定响应，只提取可用于冒烟判断的非敏感字段。"""

    if not isinstance(payload, dict):
        raise OperationsLiveCheckError("Agent 返回不是 JSON 对象")
    route = payload.get("route")
    status = payload.get("status", "COMPLETED")
    tool_steps = payload.get("tool_steps")
    answer = payload.get("answer")
    if route != "OPERATIONS":
        raise OperationsLiveCheckError(f"真实请求未进入 OPERATIONS 路由，实际路由: {route!r}")
    if status != "COMPLETED":
        raise OperationsLiveCheckError(f"经营请求未完成，实际状态: {status!r}")
    if not isinstance(tool_steps, int) or tool_steps < 1:
        raise OperationsLiveCheckError("经营请求没有完成真实工具调用")
    if not isinstance(answer, str) or not answer.strip():
        raise OperationsLiveCheckError("Agent 返回了空的经营分析结果")
    # 仅用于终端定位，去掉换行并限制长度，避免把完整业务报表复制进日志。
    answer_preview = " ".join(answer.split())[:200]
    return str(route), str(status), tool_steps, answer_preview


async def run_live_check(args: argparse.Namespace) -> None:
    """调用真实 Agent API 并验证它确实完成了经营工具调用。"""

    if args.timeout_seconds <= 0:
        raise OperationsLiveCheckError("--timeout-seconds 必须大于 0")
    signed_context = _require_context()
    conversation_id = args.conversation_id or f"operations-live-check-{uuid.uuid4().hex}"
    request_id = f"operations-live-check-{uuid.uuid4().hex}"
    endpoint = args.endpoint.rstrip("/") + "/api/v1/agent/chat"

    try:
        async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers={
                    "X-Agent-Context": signed_context,
                    "X-Request-ID": request_id,
                    "X-Trace-ID": request_id,
                },
                json={
                    "conversation_id": conversation_id,
                    "message": args.message,
                    "locale": "zh-CN",
                },
            )
    except httpx.HTTPError as exc:
        raise OperationsLiveCheckError(
            f"无法连接 Agent API，请检查服务和地址: {args.endpoint}"
        ) from exc

    if response.status_code >= 400:
        # 不输出 response.text，避免把内部异常、业务数据或网关响应复制到终端日志。
        raise OperationsLiveCheckError(
            f"Agent API 返回 HTTP {response.status_code}，request_id={request_id}；"
            "请查看 Agent/Gateway 结构化日志"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise OperationsLiveCheckError("Agent API 返回了非 JSON 响应") from exc

    route, status, tool_steps, answer_preview = _response_summary(payload)
    print("经营真实联调通过")
    print(f"route={route} status={status} tool_steps={tool_steps}")
    print(f"request_id={request_id}")
    print(f"answer_preview={answer_preview}")


def main() -> int:
    """命令行入口：失败返回非零状态，供 CI 或发布检查直接使用。"""

    try:
        asyncio.run(run_live_check(build_parser().parse_args()))
    except OperationsLiveCheckError as exc:
        print(f"经营真实联调失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
