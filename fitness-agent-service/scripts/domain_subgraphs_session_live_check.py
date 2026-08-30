"""真实验证同一会话连续切换四个 LangGraph 领域子图的只读链路。

与 ``domain_subgraphs_live_check.py`` 的四个独立会话不同，本脚本故意复用同一个
``conversation_id``。每一轮都显式要求切换到下一个领域，并通过真实只读工具查询动态
事实，验证 PostgreSQL Checkpoint 保存的对话历史不会导致路由、工具白名单或权限上下文
串域。脚本不批准确认单，也不执行任何预约、训练计划、Memory 或客服工单写操作。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


class SessionSubgraphsLiveCheckError(RuntimeError):
    """同会话跨领域真实联调未达到预期。"""


@dataclass(frozen=True)
class SessionCase:
    """同一会话中的一轮只读跨领域请求。"""

    name: str
    route: str
    message: str


DEFAULT_CASES: tuple[SessionCase, ...] = (
    SessionCase(
        name="fitness",
        route="FITNESS_COACHING",
        message="先进入健身指导场景，请调用工具查询当前登录用户信息并简要说明，只读，不要执行任何写操作。",
    ),
    SessionCase(
        name="booking",
        route="BOOKING",
        message="现在切换到课程预约场景，请仅调用只读工具查询当前机构可用课程列表，不要创建、改约或取消预约。",
    ),
    SessionCase(
        name="operations",
        route="OPERATIONS",
        message="现在切换到经营分析场景，请统计 2026-08-01 至 2026-08-15 的营收金额按周趋势，只读查询。",
    ),
    SessionCase(
        name="customer-service",
        route="CUSTOMER_SERVICE",
        message="现在切换到健身客服场景，请仅调用只读工具查询我的预约记录，不要提交客服工单。",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """构造命令参数；签名 AgentContext 只从环境变量读取。"""

    parser = argparse.ArgumentParser(description="同一会话跨四领域子图真实只读 smoke 验收")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--conversation-id",
        default=None,
        help="可选会话 ID；不传时生成一次性会话，避免污染已有会话",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="每轮真实 DeepSeek 请求的超时时间，默认 90 秒",
    )
    return parser


def require_context() -> str:
    """读取签名上下文，但不在输出或异常中泄露 Token。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise SessionSubgraphsLiveCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请先生成当前本地机构管理员的短时 AgentContext"
        )
    return context


def validate_cases(cases: tuple[SessionCase, ...]) -> None:
    """确保顺序覆盖四个领域，且每一轮都没有主动写入意图。"""

    expected = (
        "FITNESS_COACHING",
        "BOOKING",
        "OPERATIONS",
        "CUSTOMER_SERVICE",
    )
    if tuple(case.route for case in cases) != expected:
        raise SessionSubgraphsLiveCheckError("同会话 smoke 必须按四领域固定顺序执行")
    write_markers = (
        "创建预约",
        "改约",
        "取消预约",
        "创建训练计划",
        "保存记忆",
        "发布训练计划",
        "提交客服工单",
        "创建客服工单",
    )
    for case in cases:
        normalized = "".join(case.message.split())
        if not normalized:
            raise SessionSubgraphsLiveCheckError(f"{case.name} 的问题不能为空")
        # 允许“不要创建/改约/取消”这种否定保护语句，但拒绝明确肯定写操作。
        if any(marker in normalized for marker in write_markers) and "不要" not in normalized:
            raise SessionSubgraphsLiveCheckError(f"{case.name} 的问题包含主动写入意图")


def validate_response(case: SessionCase, payload: Any) -> tuple[str, int, str]:
    """校验每轮公开响应，避免把完整业务数据复制到验收日志。"""

    if not isinstance(payload, dict):
        raise SessionSubgraphsLiveCheckError(f"{case.name} 返回不是 JSON 对象")
    if payload.get("route") != case.route:
        raise SessionSubgraphsLiveCheckError(
            f"{case.name} 路由错误：expected={case.route}, actual={payload.get('route')!r}"
        )
    if payload.get("status", "COMPLETED") != "COMPLETED":
        raise SessionSubgraphsLiveCheckError(
            f"{case.name} 未完成：status={payload.get('status')!r}"
        )
    if payload.get("confirmation_id") is not None:
        raise SessionSubgraphsLiveCheckError(f"{case.name} 只读请求意外生成确认单")
    tool_steps = payload.get("tool_steps")
    if not isinstance(tool_steps, int) or tool_steps < 1:
        raise SessionSubgraphsLiveCheckError(
            f"{case.name} 没有完成真实工具调用：tool_steps={tool_steps!r}"
        )
    conversation_id = payload.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise SessionSubgraphsLiveCheckError(f"{case.name} 响应缺少 conversation_id")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise SessionSubgraphsLiveCheckError(f"{case.name} 返回空回答")
    return conversation_id, tool_steps, " ".join(answer.split())[:120]


async def run_live_check(
    args: argparse.Namespace,
    *,
    cases: tuple[SessionCase, ...] = DEFAULT_CASES,
    client: httpx.AsyncClient | None = None,
) -> None:
    """使用同一个会话 ID 依次执行四轮真实只读请求。"""

    if args.timeout_seconds <= 0:
        raise SessionSubgraphsLiveCheckError("--timeout-seconds 必须大于 0")
    validate_cases(cases)
    context = require_context()
    conversation_id = args.conversation_id or f"domain-subgraphs-session-{uuid4().hex}"
    endpoint = str(args.endpoint).rstrip("/") + "/api/v1/agent/chat"
    owned_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=float(args.timeout_seconds),
        trust_env=False,
    )
    try:
        for case in cases:
            request_id = f"domain-subgraphs-session-{case.name}-{uuid4().hex}"
            try:
                response = await active_client.post(
                    endpoint,
                    headers={
                        "X-Agent-Context": context,
                        "X-Request-ID": request_id,
                        "X-Trace-ID": request_id,
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": case.message,
                        "locale": "zh-CN",
                    },
                )
            except httpx.HTTPError as exc:
                raise SessionSubgraphsLiveCheckError(
                    f"{case.name} 无法连接 Agent API；请检查 Agent、Gateway 和下游服务"
                ) from exc
            if response.status_code >= 400:
                raise SessionSubgraphsLiveCheckError(
                    f"{case.name} 返回 HTTP {response.status_code}，request_id={request_id}；"
                    "请查看 Agent/Gateway 结构化日志"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SessionSubgraphsLiveCheckError(f"{case.name} 返回非 JSON 响应") from exc
            response_conversation_id, tool_steps, answer_preview = validate_response(case, payload)
            if response_conversation_id != conversation_id:
                raise SessionSubgraphsLiveCheckError(
                    f"{case.name} 响应会话 ID 与请求不一致，跨会话持久化校验失败"
                )
            print(
                f"[通过] {case.name}: route={case.route} status=COMPLETED "
                f"tool_steps={tool_steps} same_conversation=true"
            )
            print(f"        answer_preview={answer_preview}")
        print("同一会话连续跨四领域 LangGraph 子图真实只读联调全部通过")
    finally:
        if owned_client:
            await active_client.aclose()


def main() -> int:
    """命令行入口，失败返回非零退出码。"""

    try:
        asyncio.run(run_live_check(build_parser().parse_args()))
    except (SessionSubgraphsLiveCheckError, ValueError) as exc:
        print(f"同会话跨领域子图真实联调失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
