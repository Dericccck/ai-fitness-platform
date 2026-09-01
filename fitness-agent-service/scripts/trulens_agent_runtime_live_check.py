"""验收正在运行的 Agent 是否真正写入独立 TruLens PostgreSQL。

默认只读取 ``/health/config``，确认当前进程加载了 OTEL、TruLens 在线导出和评测库
配置；传入 ``--execute`` 后才会发送一次只读 Fitness 问题，并比较评测库的事件数量。
脚本不会输出 AgentContext、数据库密码、用户资料或完整回答，也不会调用任何写工具。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
import psycopg

DEFAULT_MESSAGE = "请说明力量训练前如何进行热身？"


class TruLensAgentRuntimeCheckError(RuntimeError):
    """正在运行的 Agent 未达到 TruLens 在线验收条件。"""


def build_parser() -> argparse.ArgumentParser:
    """构造运行时验收参数。"""

    parser = argparse.ArgumentParser(description="验收 Agent 到 TruLens PostgreSQL 的真实链路")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("TRULENS_DATABASE_URL", ""),
        help="只在 --execute 时用于核对事件数量的 TruLens PostgreSQL 地址",
    )
    parser.add_argument(
        "--message",
        default=os.getenv("AGENT_LIVE_MESSAGE", DEFAULT_MESSAGE),
        help="只读 Fitness 问题，默认验证热身知识检索",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="HTTP 请求超时时间，默认 90 秒",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=float(os.getenv("TRULENS_LIVE_WAIT_SECONDS", "20")),
        help="请求完成后等待异步导出写库的最长时间，默认 20 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="发送一次只读 Fitness 请求并核对 trulens_events 数量增加",
    )
    return parser


def _require_context() -> str:
    """读取调用者提供的签名上下文，只验证存在且不打印内容。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise TruLensAgentRuntimeCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请先生成认证服务签发的短时 AgentContext"
        )
    return context


def _database_target(database_url: str) -> dict[str, object]:
    """将异步 SQLAlchemy URL 转成安全的数据库目标摘要。"""

    normalized = database_url.replace("postgresql+asyncpg://", "postgresql://", 1).strip()
    if not normalized:
        raise TruLensAgentRuntimeCheckError("--execute 需要 TRULENS_DATABASE_URL")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise TruLensAgentRuntimeCheckError("TruLens 在线验收只允许使用 PostgreSQL 数据库")
    if not parsed.hostname or not parsed.path.strip("/"):
        raise TruLensAgentRuntimeCheckError("TRULENS_DATABASE_URL 缺少主机或数据库名")
    return {"url": normalized, "host": parsed.hostname, "port": parsed.port or 5432}


def _validate_config(payload: Any) -> dict[str, object]:
    """校验健康接口的脱敏观测配置，不接受只配置了半条链路的状态。"""

    if not isinstance(payload, dict):
        raise TruLensAgentRuntimeCheckError("/health/config 返回不是 JSON 对象")
    observability = payload.get("observability")
    if not isinstance(observability, dict):
        raise TruLensAgentRuntimeCheckError(
            "当前 Agent 未提供 observability 配置摘要；请重启到包含本次版本的代码"
        )
    otel = observability.get("otel")
    trulens = observability.get("trulens")
    if not isinstance(otel, dict) or not isinstance(trulens, dict):
        raise TruLensAgentRuntimeCheckError("/health/config 的观测配置摘要结构不完整")
    if otel.get("configured") is not True:
        raise TruLensAgentRuntimeCheckError("当前 Agent 未启用 OTEL Trace 导出")
    if trulens.get("enabled") is not True:
        raise TruLensAgentRuntimeCheckError("当前 Agent 未启用 TruLens 采集")
    if trulens.get("online_export_enabled") is not True:
        raise TruLensAgentRuntimeCheckError("当前 Agent 未启用 TruLens 在线数据库导出")
    if trulens.get("capture_mode") not in {"metadata", "evaluation"}:
        raise TruLensAgentRuntimeCheckError("当前 Agent 的 TruLens capture mode 未启用")
    database = trulens.get("database")
    if not isinstance(database, dict) or database.get("scheme") not in {"postgresql", "postgres"}:
        raise TruLensAgentRuntimeCheckError("当前 Agent 的 TruLens 目标不是 PostgreSQL")
    return {"capture_mode": trulens["capture_mode"], "database": database}


def _sync_database_url(database_url: str) -> str:
    """把脚本输入统一为 psycopg 可识别的 PostgreSQL URL。"""

    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1).strip()


def _event_count(database_url: str) -> int:
    """读取事件总数；只执行 SELECT，不修改评测库。"""

    try:
        with (
            psycopg.connect(_sync_database_url(database_url), connect_timeout=5) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT COUNT(*) FROM trulens_events")
            row = cursor.fetchone()
    except psycopg.Error as exc:
        raise TruLensAgentRuntimeCheckError(
            "无法读取 TruLens PostgreSQL；请检查库已启动、URL 和权限"
        ) from exc
    if not row or not isinstance(row[0], int):
        raise TruLensAgentRuntimeCheckError("TruLens 事件表返回了异常计数")
    return row[0]


def _validate_agent_response(payload: Any) -> None:
    """只校验路由和完成状态，不把回答正文写入日志。"""

    if not isinstance(payload, dict):
        raise TruLensAgentRuntimeCheckError("Agent 业务响应不是 JSON 对象")
    if payload.get("route") != "FITNESS_COACHING":
        raise TruLensAgentRuntimeCheckError(
            f"只读 Fitness 请求未进入 FITNESS_COACHING 路由，实际路由={payload.get('route')!r}"
        )
    if payload.get("status") != "COMPLETED":
        raise TruLensAgentRuntimeCheckError("只读 Fitness 请求未完成")
    if not isinstance(payload.get("answer"), str) or not payload["answer"].strip():
        raise TruLensAgentRuntimeCheckError("只读 Fitness 请求返回空回答")


def _request_headers(context: str, request_id: str) -> dict[str, str]:
    """构造最小受控请求头。"""

    return {
        "X-Agent-Context": context,
        "X-Request-ID": request_id,
        "X-Trace-ID": request_id,
    }


def _execute_agent_request(args: argparse.Namespace, context: str, before: int) -> int:
    """执行一次只读请求并等待异步 OTEL exporter 写入事件表。"""

    request_id = f"trulens-agent-live-{uuid.uuid4().hex}"
    endpoint = args.endpoint.rstrip("/") + "/api/v1/agent/chat"
    try:
        with httpx.Client(timeout=args.timeout_seconds, trust_env=False) as client:
            response = client.post(
                endpoint,
                headers=_request_headers(context, request_id),
                json={
                    "conversation_id": request_id,
                    "message": args.message,
                    "locale": "zh-CN",
                },
            )
    except httpx.HTTPError as exc:
        raise TruLensAgentRuntimeCheckError("无法连接 Agent 业务接口") from exc
    if response.status_code >= 400:
        raise TruLensAgentRuntimeCheckError(
            f"Agent 只读请求返回 HTTP {response.status_code}，request_id={request_id}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TruLensAgentRuntimeCheckError("Agent 业务接口返回了非 JSON 响应") from exc
    _validate_agent_response(payload)

    deadline = time.monotonic() + args.wait_seconds
    current = before
    while time.monotonic() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        current = _event_count(args.database_url)
        if current > before:
            print(f"真实 Agent→TruLens 联调通过: events={before}->{current}")
            print(f"request_id={request_id}")
            return current
    raise TruLensAgentRuntimeCheckError(
        f"Agent 请求已完成，但 TruLens 事件数未增加: events={before}->{current}；"
        "请检查当前进程是否已重启并加载在线导出配置"
    )


def run_check(args: argparse.Namespace) -> None:
    """执行配置检查，按需执行一次真实只读业务请求。"""

    if args.timeout_seconds <= 0 or args.wait_seconds < 0:
        raise TruLensAgentRuntimeCheckError("超时时间必须大于 0，等待时间不能小于 0")
    context = _require_context() if args.execute else os.getenv("AGENT_LIVE_AGENT_CONTEXT", "")
    try:
        with httpx.Client(timeout=min(args.timeout_seconds, 10.0), trust_env=False) as client:
            response = client.get(args.endpoint.rstrip("/") + "/health/config")
    except httpx.HTTPError as exc:
        raise TruLensAgentRuntimeCheckError("无法连接 Agent 健康接口") from exc
    if response.status_code >= 400:
        raise TruLensAgentRuntimeCheckError(f"Agent 健康接口返回 HTTP {response.status_code}")
    try:
        config_summary = _validate_config(response.json())
    except ValueError as exc:
        raise TruLensAgentRuntimeCheckError("Agent 健康接口返回了非 JSON 响应") from exc
    print(
        "Agent TruLens 配置已加载: "
        f"capture_mode={config_summary['capture_mode']} "
        f"database={config_summary['database'].get('host')}"
    )
    if not args.execute:
        print("仅配置检查通过；如需核对真实 Record，请追加 --execute")
        return
    _database_target(args.database_url)
    before = _event_count(args.database_url)
    _execute_agent_request(args, context, before)


def main() -> int:
    """命令行入口：失败返回非零状态，供发布验收使用。"""

    try:
        run_check(build_parser().parse_args())
    except TruLensAgentRuntimeCheckError as exc:
        print(f"Agent TruLens 运行时验收失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
