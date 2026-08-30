"""真实验证 Supervisor 和四个 LangGraph 领域子图的只读链路。

脚本只调用已启动的 Agent HTTP API，不直接访问数据库，也不批准任何确认单。四条
问题都明确要求查询动态事实，使 DeepSeek 必须经过目标领域子图和至少一个只读工具；
如果模型只用自然语言回答、进入错误路由或意外生成写操作确认，验收立即失败。

该检查证明真实模型、Supervisor 路由、领域 Prompt、工具白名单、Tool Registry、Java
Gateway 和业务事实查询可以协同工作。跨领域恶意工具隔离仍由离线测试精确覆盖，因为
生产 HTTP 响应不应暴露模型可见工具目录或内部 LangGraph namespace。
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


class DomainSubgraphsLiveCheckError(RuntimeError):
    """四领域真实联调未达到安全通过条件。"""


@dataclass(frozen=True)
class SmokeCase:
    """一条领域子图的只读验收用例。"""

    name: str
    route: str
    message: str
    forbidden_answer_markers: tuple[str, ...] = ()


DEFAULT_CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        name="fitness",
        route="FITNESS_COACHING",
        message="在健身指导场景，请调用工具查询当前登录用户信息并简要说明，不要执行任何写操作。",
    ),
    SmokeCase(
        name="booking",
        route="BOOKING",
        message="为了课程预约，请仅调用只读工具查询当前机构可用课程列表。",
        forbidden_answer_markers=("需要查询机构", "还需要查询", "无法查询课程", "未能查询课程"),
    ),
    SmokeCase(
        name="operations",
        route="OPERATIONS",
        message="统计 2026-08-01 至 2026-08-15 的营收金额按周趋势。",
    ),
    SmokeCase(
        name="customer-service",
        route="CUSTOMER_SERVICE",
        message="请在健身客服场景仅调用只读工具查询我的预约记录。",
    ),
)

_WRITE_INTENT_MARKERS = (
    "帮我预约",
    "创建预约",
    "改约到",
    "取消我的预约",
    "创建训练计划",
    "保存记忆",
    "发布训练计划",
    "提交客服工单",
    "创建客服工单",
)


def build_parser() -> argparse.ArgumentParser:
    """构造命令参数；签名 AgentContext 只能从环境变量读取。"""

    parser = argparse.ArgumentParser(description="Supervisor 四领域子图真实只读 smoke 验收")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="每条真实 DeepSeek 请求的超时时间，默认 90 秒",
    )
    return parser


def require_context() -> str:
    """读取签名上下文，但绝不把 Token 写入日志或异常。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise DomainSubgraphsLiveCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请先生成当前本地机构管理员的短时 AgentContext"
        )
    return context


def validate_cases(cases: tuple[SmokeCase, ...]) -> None:
    """在发请求前验证用例仍保持四领域唯一且没有主动写入意图。"""

    expected_routes = {
        "FITNESS_COACHING",
        "BOOKING",
        "OPERATIONS",
        "CUSTOMER_SERVICE",
    }
    routes = {case.route for case in cases}
    if routes != expected_routes or len(cases) != len(expected_routes):
        raise DomainSubgraphsLiveCheckError("smoke 用例必须一一覆盖四个领域路由")
    for case in cases:
        normalized = "".join(case.message.split())
        if not normalized:
            raise DomainSubgraphsLiveCheckError(f"{case.name} 的问题不能为空")
        if any(marker in normalized for marker in _WRITE_INTENT_MARKERS):
            raise DomainSubgraphsLiveCheckError(
                f"{case.name} 的问题包含写入意图，拒绝执行只读 smoke"
            )


def validate_response(case: SmokeCase, payload: Any) -> tuple[int, str]:
    """校验稳定公开字段，不输出完整业务结果或内部模型响应。"""

    if not isinstance(payload, dict):
        raise DomainSubgraphsLiveCheckError(f"{case.name} 返回不是 JSON 对象")
    route = payload.get("route")
    if route != case.route:
        raise DomainSubgraphsLiveCheckError(
            f"{case.name} 路由错误：expected={case.route}, actual={route!r}"
        )
    status = payload.get("status", "COMPLETED")
    if status != "COMPLETED":
        raise DomainSubgraphsLiveCheckError(f"{case.name} 意外进入非完成状态：status={status!r}")
    if payload.get("confirmation_id") is not None:
        raise DomainSubgraphsLiveCheckError(f"{case.name} 只读请求意外生成确认单")
    tool_steps = payload.get("tool_steps")
    if not isinstance(tool_steps, int) or tool_steps < 1:
        raise DomainSubgraphsLiveCheckError(
            f"{case.name} 未完成真实工具调用：tool_steps={tool_steps!r}"
        )
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise DomainSubgraphsLiveCheckError(f"{case.name} 返回空回答")
    compact_answer = "".join(answer.split())
    if any(marker in compact_answer for marker in case.forbidden_answer_markers):
        raise DomainSubgraphsLiveCheckError(f"{case.name} 返回的是未完成查询说明，不是真实业务结果")
    # 只展示极短预览，帮助判断回答不是空壳，同时避免把完整用户或经营数据复制到日志。
    return tool_steps, " ".join(answer.split())[:120]


async def run_live_check(
    args: argparse.Namespace,
    *,
    cases: tuple[SmokeCase, ...] = DEFAULT_CASES,
    client: httpx.AsyncClient | None = None,
) -> None:
    """依次执行四条真实请求；任一失败则整轮失败。"""

    if args.timeout_seconds <= 0:
        raise DomainSubgraphsLiveCheckError("--timeout-seconds 必须大于 0")
    validate_cases(cases)
    context = require_context()
    endpoint = str(args.endpoint).rstrip("/") + "/api/v1/agent/chat"
    owned_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=float(args.timeout_seconds),
        # 本地服务验收不应被系统 HTTP_PROXY 转发到外部代理。
        trust_env=False,
    )
    try:
        for case in cases:
            request_id = f"domain-subgraph-{case.name}-{uuid4().hex}"
            try:
                response = await active_client.post(
                    endpoint,
                    headers={
                        "X-Agent-Context": context,
                        "X-Request-ID": request_id,
                        "X-Trace-ID": request_id,
                    },
                    json={
                        "conversation_id": request_id,
                        "message": case.message,
                        "locale": "zh-CN",
                    },
                )
            except httpx.HTTPError as exc:
                raise DomainSubgraphsLiveCheckError(
                    f"{case.name} 无法连接 Agent API；请检查 Agent、Gateway 和下游服务"
                ) from exc
            if response.status_code >= 400:
                raise DomainSubgraphsLiveCheckError(
                    f"{case.name} 返回 HTTP {response.status_code}，request_id={request_id}；"
                    "请查看 Agent/Gateway 结构化日志"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise DomainSubgraphsLiveCheckError(f"{case.name} 返回非 JSON 响应") from exc
            tool_steps, answer_preview = validate_response(case, payload)
            print(
                f"[通过] {case.name}: route={case.route} status=COMPLETED tool_steps={tool_steps}"
            )
            print(f"        answer_preview={answer_preview}")
        print("Supervisor 四领域 LangGraph 子图真实只读联调全部通过")
    finally:
        if owned_client:
            await active_client.aclose()


def main() -> int:
    """命令行入口，错误信息保持脱敏并返回非零退出码。"""

    try:
        asyncio.run(run_live_check(build_parser().parse_args()))
    except (DomainSubgraphsLiveCheckError, ValueError) as exc:
        print(f"四领域子图真实联调失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
