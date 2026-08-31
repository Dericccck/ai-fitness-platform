"""预约/健身真实联调脚本共用的安全 HTTP 辅助函数。

真实联调和单元测试的边界不同：脚本会访问正在运行的 Agent、Java Gateway 和业务
数据库，因此默认只验证 Agent 能否生成正确的确认单。只有调用者显式提供 ``--execute``
时，脚本才会批准确认单并触发真实写操作，避免把“检查服务是否可用”误变成业务数据写入。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


class BusinessLiveCheckError(RuntimeError):
    """真实预约/健身联调未达到预期。"""


@dataclass(frozen=True)
class LiveCheckConfig:
    """一轮联调所需的非敏感配置。"""

    endpoint: str
    context: str
    message: str
    expected_route: str
    expected_action_prefix: tuple[str, ...]
    timeout_seconds: float
    poll_interval_seconds: float
    poll_timeout_seconds: float
    execute: bool
    keep_confirmation: bool


def add_common_arguments(parser: argparse.ArgumentParser, *, description: str) -> None:
    """给预约/健身包装脚本增加统一参数。"""

    parser.description = description
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--message",
        default=None,
        help="用于联调的自然语言请求；默认从业务专用环境变量读取",
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
        help="单次 HTTP 请求超时时间，默认 90 秒",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "60")),
        help="批准后等待业务写入完成的最长时间，默认 60 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.getenv("AGENT_LIVE_EXECUTE_WRITES") == "1",
        help="显式批准并执行确认单；默认只检查确认流程，不写业务数据",
    )
    parser.add_argument(
        "--keep-confirmation",
        action="store_true",
        default=os.getenv("AGENT_LIVE_KEEP_CONFIRMATION") == "1",
        help="保留默认检查生成的 PENDING 确认单，便于手动打开确认页面",
    )


def require_context() -> str:
    """读取认证服务签发的 AgentContext，不打印 Token。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise BusinessLiveCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请先设置认证服务签发的 AgentContext"
        )
    return context


def build_config(
    args: argparse.Namespace,
    *,
    message_env: str,
    route: str,
    action_prefix: tuple[str, ...],
) -> LiveCheckConfig:
    """组合并校验命令行与环境变量配置。"""

    message = (args.message or os.getenv(message_env, "")).strip()
    if not message:
        raise BusinessLiveCheckError(
            f"缺少联调请求；请通过 --message 或 {message_env} 提供安全的测试业务请求"
        )
    if args.timeout_seconds <= 0 or args.poll_timeout_seconds <= 0:
        raise BusinessLiveCheckError("timeout 参数必须大于 0")
    return LiveCheckConfig(
        endpoint=args.endpoint.rstrip("/"),
        context=require_context(),
        message=message,
        expected_route=route,
        expected_action_prefix=action_prefix,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=min(1.0, max(0.1, args.poll_timeout_seconds / 20)),
        poll_timeout_seconds=args.poll_timeout_seconds,
        execute=bool(args.execute),
        keep_confirmation=bool(args.keep_confirmation),
    )


def _request_headers(config: LiveCheckConfig, request_id: str) -> dict[str, str]:
    """构造不包含敏感值日志的 Agent 请求头。"""

    return {
        "X-Agent-Context": config.context,
        "X-Request-ID": request_id,
        "X-Trace-ID": request_id,
    }


def _require_object(payload: Any, *, operation: str) -> dict[str, Any]:
    """把 HTTP JSON 响应收敛为安全的对象校验错误。"""

    if not isinstance(payload, dict):
        raise BusinessLiveCheckError(f"{operation} 返回不是 JSON 对象")
    return payload


async def _post_chat(
    client: httpx.AsyncClient,
    config: LiveCheckConfig,
    *,
    conversation_id: str,
    request_id: str,
) -> dict[str, Any]:
    """调用 Agent 对话接口并屏蔽错误响应正文。"""

    try:
        response = await client.post(
            config.endpoint + "/api/v1/agent/chat",
            headers=_request_headers(config, request_id),
            json={
                "conversation_id": conversation_id,
                "message": config.message,
                "locale": "zh-CN",
            },
        )
    except httpx.HTTPError as exc:
        raise BusinessLiveCheckError("无法连接 Agent API，请检查 Agent 服务和地址") from exc
    if response.status_code >= 400:
        raise BusinessLiveCheckError(
            f"Agent API 返回 HTTP {response.status_code}，request_id={request_id}；"
            "请查看 Agent/Gateway 结构化日志"
        )
    try:
        return _require_object(response.json(), operation="Agent API")
    except ValueError as exc:
        raise BusinessLiveCheckError("Agent API 返回了非 JSON 响应") from exc


def _validate_confirmation_response(
    payload: dict[str, Any], config: LiveCheckConfig
) -> tuple[str, str]:
    """确认请求确实进入目标路由并生成了待确认写操作。"""

    if payload.get("route") != config.expected_route:
        raise BusinessLiveCheckError(
            f"请求未进入 {config.expected_route} 路由，实际路由={payload.get('route')!r}"
        )
    if payload.get("status") != "CONFIRMATION_REQUIRED":
        raise BusinessLiveCheckError(
            "请求没有进入 CONFIRMATION_REQUIRED；请确认测试请求明确要求执行写操作，"
            f"实际状态={payload.get('status')!r}"
        )
    confirmation_id = payload.get("confirmation_id")
    if not isinstance(confirmation_id, str) or not confirmation_id:
        raise BusinessLiveCheckError("响应缺少 confirmation_id")
    summary = payload.get("confirmation_summary")
    if not isinstance(summary, dict):
        raise BusinessLiveCheckError("响应缺少脱敏 confirmation_summary")
    action = summary.get("action")
    if not isinstance(action, str) or not action.startswith(config.expected_action_prefix):
        raise BusinessLiveCheckError(
            f"确认单动作不属于 {config.expected_action_prefix}，实际 action={action!r}"
        )
    return confirmation_id, str(summary.get("operation", "待执行写操作"))


async def _get_confirmation(
    client: httpx.AsyncClient, config: LiveCheckConfig, confirmation_id: str
) -> dict[str, Any]:
    """读取脱敏确认单，支持批准后的最终状态轮询。"""

    try:
        response = await client.get(
            config.endpoint + f"/api/v1/agent/confirmations/{confirmation_id}",
            headers={"X-Agent-Context": config.context},
        )
    except httpx.HTTPError as exc:
        raise BusinessLiveCheckError("无法读取确认单状态") from exc
    if response.status_code >= 400:
        raise BusinessLiveCheckError(f"确认单查询返回 HTTP {response.status_code}")
    try:
        return _require_object(response.json(), operation="确认单查询")
    except ValueError as exc:
        raise BusinessLiveCheckError("确认单查询返回了非 JSON 响应") from exc


async def _decide_confirmation(
    client: httpx.AsyncClient,
    config: LiveCheckConfig,
    confirmation_id: str,
    request_id: str,
    *,
    decision: str,
) -> dict[str, Any]:
    """通过正式确认 API 提交决定，绝不把参数或 Token 放到请求体。"""

    try:
        response = await client.post(
            config.endpoint + f"/api/v1/agent/confirmations/{confirmation_id}/decisions",
            headers={
                "X-Agent-Context": config.context,
                "X-Trace-ID": request_id,
            },
            json={"decision": decision, "decision_request_id": request_id},
        )
    except httpx.HTTPError as exc:
        raise BusinessLiveCheckError("无法提交确认决定") from exc
    if response.status_code >= 400:
        raise BusinessLiveCheckError(f"确认决定返回 HTTP {response.status_code}")
    try:
        return _require_object(response.json(), operation="确认决定")
    except ValueError as exc:
        raise BusinessLiveCheckError("确认批准返回了非 JSON 响应") from exc


async def run_business_live_check(config: LiveCheckConfig, *, label: str) -> None:
    """执行“生成确认单→可选批准→轮询最终事实”的一轮业务联调。"""

    conversation_id = f"{label.lower()}-live-check-{uuid.uuid4().hex}"
    request_id = f"{label.lower()}-live-check-{uuid.uuid4().hex}"
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await _post_chat(
            client,
            config,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        confirmation_id, operation = _validate_confirmation_response(response, config)
        current = await _get_confirmation(client, config, confirmation_id)
        if current.get("authorization_status") != "PENDING":
            raise BusinessLiveCheckError(
                f"确认单初始状态不是 PENDING，实际={current.get('authorization_status')!r}"
            )

        if not config.execute:
            print(f"{label} 确认流程检查通过（未执行真实写操作）")
            if config.keep_confirmation:
                print(f"confirmation_id={confirmation_id}")
                print(f"operation={operation}")
                print("确认单仍为 PENDING；如需执行，请在确认测试数据安全后追加 --execute")
            else:
                rejected = await _decide_confirmation(
                    client,
                    config,
                    confirmation_id,
                    request_id + "-cleanup",
                    decision="REJECT",
                )
                if rejected.get("authorization_status") != "REJECTED":
                    raise BusinessLiveCheckError("联调清理确认单失败，未返回 REJECTED")
                print("测试确认单已自动拒绝并清理，不会留下 PENDING 记录")
            return

        approved = await _decide_confirmation(
            client,
            config,
            confirmation_id,
            request_id,
            decision="APPROVE",
        )
        if approved.get("authorization_status") != "APPROVED":
            raise BusinessLiveCheckError("批准接口没有返回 APPROVED")

        deadline = time.monotonic() + config.poll_timeout_seconds
        final = approved
        while time.monotonic() < deadline:
            final = await _get_confirmation(client, config, confirmation_id)
            if final.get("execution_status") in {"SUCCEEDED", "FAILED_FINAL", "RETRYABLE_FAILED"}:
                break
            await asyncio.sleep(config.poll_interval_seconds)
        execution_status = final.get("execution_status")
        if execution_status != "SUCCEEDED":
            raise BusinessLiveCheckError(
                f"确认单未成功执行，execution_status={execution_status!r}；"
                "请根据 request_id 查看 Agent/Gateway/业务服务结构化日志"
            )
        print(f"{label} 真实联调通过")
        print(f"confirmation_id={confirmation_id}")
        print(f"operation={operation} execution_status=SUCCEEDED")
        print(f"request_id={request_id}")


def run_main(factory: Any, *, label: str) -> int:
    """包装命令行异常，确保日志不包含响应正文和签名上下文。"""

    try:
        args, message_env, route, action_prefix = factory()
        config = build_config(
            args,
            message_env=message_env,
            route=route,
            action_prefix=action_prefix,
        )
        asyncio.run(run_business_live_check(config, label=label))
    except (BusinessLiveCheckError, ValueError) as exc:
        print(f"{label} 真实联调失败：{exc}", file=sys.stderr)
        return 1
    return 0
