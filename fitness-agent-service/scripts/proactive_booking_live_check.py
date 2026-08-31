"""验收预约事件驱动的主动提醒真实链路。

默认只检查 Agent、Gateway、PostgreSQL、Redis 和预约服务的可用性，不会写入预约。
只有调用者显式传入 ``--execute``（或设置 ``AGENT_LIVE_EXECUTE_WRITES=1``）时，才会
批准预约确认单并触发一次真实的本地测试预约。写入完成后，本脚本会轮询 Agent PostgreSQL，
确认预约 Outbox 已发布，预约事件已经依次进入事件 Inbox、生成通知 Outbox，并由站内通知 Worker 投递到收件箱；
随后会重投同一个事件，确认幂等键不会产生第二条通知。

本脚本不直接删除预约，也不绕过预约业务服务做 SQL 清理。执行真实写入前，调用者必须
使用专门的本地测试数据；验收完成后应通过已有的取消预约确认流程清理测试预约，避免测试工具
拥有超出业务权限边界的数据库删除能力。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aio_pika
import asyncpg  # type: ignore[import-untyped]
import httpx


class ProactiveBookingLiveCheckError(RuntimeError):
    """主动提醒真实验收未达到预期。"""


@dataclass(frozen=True)
class LiveCheckConfig:
    """一次真实验收所需的非敏感运行参数。"""

    endpoint: str
    gateway_endpoint: str
    booking_endpoint: str
    rabbitmq_url: str
    context: str
    database_url: str
    organization_id: str
    message: str
    timeout_seconds: float
    poll_timeout_seconds: float
    execute: bool
    booking_mysql_container: str
    booking_mysql_database: str
    booking_mysql_username: str
    booking_mysql_password: str
    event_exchange: str


@dataclass(frozen=True)
class ProactiveChainResult:
    """从 Agent PostgreSQL 读取的主动提醒链路结果。"""

    event_id: str
    aggregate_id: str
    event_status: str
    notification_outbox_count: int
    published_notification_count: int
    in_app_notification_count: int
    expected_notification_count: int


@dataclass(frozen=True)
class BookingOutboxEvent:
    """从预约 MySQL 只读查询到的本轮预约 Outbox 事实。"""

    event_key: str
    event_type: str
    aggregate_id: str
    organization_id: str
    status: str
    payload: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数；认证上下文只从环境变量读取，不允许通过参数泄漏。"""

    parser = argparse.ArgumentParser(description="验收预约主动提醒真实链路")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，默认读取 AGENT_LIVE_API_URL",
    )
    parser.add_argument(
        "--message",
        default=os.getenv("AGENT_PROACTIVE_BOOKING_LIVE_MESSAGE", ""),
        help="本地测试预约请求；默认读取 AGENT_PROACTIVE_BOOKING_LIVE_MESSAGE",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8081"),
        help="Java Gateway 地址，默认读取 AGENT_GATEWAY_BASE_URL",
    )
    parser.add_argument(
        "--booking-url",
        default=os.getenv("AGENT_BOOKING_SERVICE_URL", "http://127.0.0.1:8083"),
        help="预约服务地址，默认读取 AGENT_BOOKING_SERVICE_URL",
    )
    parser.add_argument(
        "--rabbitmq-url",
        default=os.getenv(
            "AGENT_PROACTIVE_RABBITMQ_URL",
            "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/",
        ),
        help="RabbitMQ 地址，默认读取 AGENT_PROACTIVE_RABBITMQ_URL",
    )
    parser.add_argument(
        "--organization-id",
        default=os.getenv("DEV_AGENT_ORG_ID", ""),
        help="事件验收所属机构，默认读取 DEV_AGENT_ORG_ID",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "AGENT_DATABASE_URL",
            "postgresql+asyncpg://fitness_agent:fitness_agent@127.0.0.1:5433/fitness_agent",
        ),
        help="Agent PostgreSQL 地址，默认读取 AGENT_DATABASE_URL",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
        help="Agent HTTP 请求超时时间，默认 90 秒",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "90")),
        help="等待事件 Inbox 和通知收件箱完成的最长时间，默认 90 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.getenv("AGENT_LIVE_EXECUTE_WRITES") == "1",
        help="显式批准并执行一次本地测试预约；默认只做链路前置检查",
    )
    parser.add_argument(
        "--booking-mysql-container",
        default=os.getenv("BOOKING_DB_CONTAINER", ""),
        help="真实写入验收使用的预约 MySQL 容器名，例如 fitness-mysql",
    )
    parser.add_argument(
        "--booking-mysql-database",
        default=os.getenv("BOOKING_DB_NAME", "fitness"),
        help="预约 MySQL 数据库名，默认 fitness",
    )
    parser.add_argument(
        "--booking-mysql-username",
        default=os.getenv("BOOKING_DB_USERNAME", ""),
        help="预约 MySQL 只读验收账号；密码只从 BOOKING_DB_PASSWORD 读取",
    )
    parser.add_argument(
        "--event-exchange",
        default=os.getenv("AGENT_PROACTIVE_RABBITMQ_EXCHANGE", "fitness.domain.events"),
        help="主动提醒事件交换机名称",
    )
    return parser


def build_config(args: argparse.Namespace) -> LiveCheckConfig:
    """校验本次验收的必填配置。"""

    context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
    if not context:
        raise ProactiveBookingLiveCheckError(
            "缺少 AGENT_LIVE_AGENT_CONTEXT；请先设置认证服务签发的 AgentContext"
        )
    if not args.message.strip():
        raise ProactiveBookingLiveCheckError(
            "缺少测试预约请求；请通过 --message 或 AGENT_PROACTIVE_BOOKING_LIVE_MESSAGE 提供"
        )
    if not args.organization_id.strip():
        raise ProactiveBookingLiveCheckError("缺少 DEV_AGENT_ORG_ID")
    if not args.database_url.strip():
        raise ProactiveBookingLiveCheckError("缺少 AGENT_DATABASE_URL")
    if args.timeout_seconds <= 0 or args.poll_timeout_seconds <= 0:
        raise ProactiveBookingLiveCheckError("timeout 参数必须大于 0")
    booking_mysql_container = str(args.booking_mysql_container).strip()
    booking_mysql_username = str(args.booking_mysql_username).strip()
    booking_mysql_password = os.getenv("BOOKING_DB_PASSWORD", "").strip()
    if args.execute and not booking_mysql_container:
        raise ProactiveBookingLiveCheckError(
            "真实链路验收必须提供 BOOKING_DB_CONTAINER，用于只读核对预约 Outbox"
        )
    if args.execute and (not booking_mysql_username or not booking_mysql_password):
        raise ProactiveBookingLiveCheckError(
            "真实链路验收必须提供 BOOKING_DB_USERNAME/BOOKING_DB_PASSWORD"
        )
    return LiveCheckConfig(
        endpoint=args.endpoint.rstrip("/"),
        gateway_endpoint=args.gateway_url.rstrip("/"),
        booking_endpoint=args.booking_url.rstrip("/"),
        rabbitmq_url=args.rabbitmq_url,
        context=context,
        database_url=args.database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        organization_id=args.organization_id.strip(),
        message=args.message.strip(),
        timeout_seconds=args.timeout_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        execute=bool(args.execute),
        booking_mysql_container=booking_mysql_container,
        booking_mysql_database=str(args.booking_mysql_database).strip(),
        booking_mysql_username=booking_mysql_username,
        booking_mysql_password=booking_mysql_password,
        event_exchange=str(args.event_exchange).strip(),
    )


def _headers(context: str, request_id: str) -> dict[str, str]:
    """构造 Agent 请求头；不把认证上下文写入日志。"""

    return {
        "X-Agent-Context": context,
        "X-Request-ID": request_id,
        "X-Trace-ID": request_id,
    }


async def _post_chat(
    client: httpx.AsyncClient, config: LiveCheckConfig, *, conversation_id: str, request_id: str
) -> dict[str, Any]:
    """提交一次预约请求并返回稳定 JSON 响应。"""

    try:
        response = await client.post(
            config.endpoint + "/api/v1/agent/chat",
            headers=_headers(config.context, request_id),
            json={
                "conversation_id": conversation_id,
                "message": config.message,
                "locale": "zh-CN",
            },
        )
    except httpx.HTTPError as exc:
        raise ProactiveBookingLiveCheckError("无法连接 Agent API") from exc
    if response.status_code >= 400:
        raise ProactiveBookingLiveCheckError(
            f"Agent API 返回 HTTP {response.status_code}，request_id={request_id}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProactiveBookingLiveCheckError("Agent API 返回了非 JSON 响应") from exc
    if not isinstance(payload, dict):
        raise ProactiveBookingLiveCheckError("Agent API 返回不是 JSON 对象")
    return payload


async def _check_http_health(
    client: httpx.AsyncClient, *, endpoint: str, label: str, ready: bool = False
) -> None:
    """检查基础服务存活；Agent 额外检查 ready，避免服务只启动但依赖未就绪。"""

    path = "/health/ready" if ready else "/health/live"
    try:
        response = await client.get(endpoint + path)
    except httpx.HTTPError as exc:
        raise ProactiveBookingLiveCheckError(f"{label} 健康检查无法连接") from exc
    if response.status_code >= 400:
        raise ProactiveBookingLiveCheckError(f"{label} 健康检查返回 HTTP {response.status_code}")


async def _run_preflight(config: LiveCheckConfig) -> None:
    """在任何真实预约写入前验证跨服务依赖，避免把基础设施故障变成业务脏数据。"""

    async with httpx.AsyncClient(timeout=min(config.timeout_seconds, 10.0)) as client:
        await _check_http_health(client, endpoint=config.endpoint, label="Agent", ready=True)
        await _check_http_health(client, endpoint=config.gateway_endpoint, label="Gateway")
        await _check_http_health(client, endpoint=config.booking_endpoint, label="预约")

    connection = await asyncpg.connect(config.database_url)
    try:
        await connection.fetchval("SELECT 1")
        await connection.fetchval("SELECT 1 FROM agent_proactive_event_inbox LIMIT 1")
    finally:
        await connection.close()

    rabbit_connection = await aio_pika.connect_robust(config.rabbitmq_url)
    await rabbit_connection.close()


def _require_confirmation(payload: dict[str, Any]) -> str:
    """确认请求确实进入预约写操作确认流程。"""

    if payload.get("route") != "BOOKING":
        raise ProactiveBookingLiveCheckError(
            f"预约请求未进入 BOOKING 路由，实际路由={payload.get('route')!r}"
        )
    if payload.get("status") != "CONFIRMATION_REQUIRED":
        raise ProactiveBookingLiveCheckError(
            "预约请求没有生成 CONFIRMATION_REQUIRED 确认单；请检查测试请求是否明确要求创建预约"
        )
    confirmation_id = payload.get("confirmation_id")
    if not isinstance(confirmation_id, str) or not confirmation_id:
        raise ProactiveBookingLiveCheckError("响应缺少 confirmation_id")
    summary = payload.get("confirmation_summary")
    if not isinstance(summary, dict) or not str(summary.get("action", "")).startswith(
        "CREATE_APPOINTMENT"
    ):
        raise ProactiveBookingLiveCheckError("确认单不是创建预约动作")
    return confirmation_id


async def _get_confirmation(
    client: httpx.AsyncClient, config: LiveCheckConfig, confirmation_id: str
) -> dict[str, Any]:
    """读取脱敏确认单状态。"""

    response = await client.get(
        config.endpoint + f"/api/v1/agent/confirmations/{confirmation_id}",
        headers={"X-Agent-Context": config.context},
    )
    if response.status_code >= 400:
        raise ProactiveBookingLiveCheckError(f"确认单查询返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProactiveBookingLiveCheckError("确认单查询返回了非 JSON 响应") from exc
    if not isinstance(payload, dict):
        raise ProactiveBookingLiveCheckError("确认单查询返回不是 JSON 对象")
    return payload


async def _decide_confirmation(
    client: httpx.AsyncClient,
    config: LiveCheckConfig,
    confirmation_id: str,
    request_id: str,
) -> None:
    """通过正式确认接口批准动作，不把业务参数放入请求体。"""

    response = await client.post(
        config.endpoint + f"/api/v1/agent/confirmations/{confirmation_id}/decisions",
        headers={"X-Agent-Context": config.context, "X-Trace-ID": request_id},
        json={"decision": "APPROVE", "decision_request_id": request_id},
    )
    if response.status_code >= 400:
        raise ProactiveBookingLiveCheckError(f"确认批准返回 HTTP {response.status_code}")


async def _wait_for_execution(
    client: httpx.AsyncClient, config: LiveCheckConfig, confirmation_id: str
) -> None:
    """等待预约写入完成，确认后续事件验收只针对已成功的业务事实。"""

    deadline = time.monotonic() + config.poll_timeout_seconds
    while time.monotonic() < deadline:
        current = await _get_confirmation(client, config, confirmation_id)
        execution_status = current.get("execution_status")
        if execution_status == "SUCCEEDED":
            return
        if execution_status in {"FAILED_FINAL", "FAILED_RETRYABLE"}:
            raise ProactiveBookingLiveCheckError(
                f"预约确认单执行失败，execution_status={execution_status!r}"
            )
        await asyncio.sleep(1)
    raise ProactiveBookingLiveCheckError("等待预约业务写入超时")


def _sql_literal(value: str) -> str:
    """把已知为字符串的验收参数安全编码为 MySQL 字符串字面量。"""

    return "'" + value.replace("'", "''") + "'"


def _run_booking_mysql_select(config: LiveCheckConfig, query: str) -> list[str]:
    """通过显式指定的容器只执行 SELECT，不允许验收脚本修改预约数据。"""

    command = [
        "docker",
        "exec",
        "-i",
        config.booking_mysql_container,
        "mysql",
        "--protocol=TCP",
        "--batch",
        "--raw",
        "--skip-column-names",
        "-u",
        config.booking_mysql_username,
        config.booking_mysql_database,
    ]
    environment = {**os.environ, "MYSQL_PWD": config.booking_mysql_password}
    result = subprocess.run(
        command,
        input=query + "\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=environment,
    )
    if result.returncode != 0:
        raise ProactiveBookingLiveCheckError("预约 MySQL 只读查询失败")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _find_booking_appointment_id(config: LiveCheckConfig, request_id: str) -> str | None:
    """通过预约业务操作表把本轮请求绑定到唯一预约 ID。"""

    query = (
        "SELECT appointment_id FROM agent_booking_operation WHERE request_id = "
        + _sql_literal(request_id)
        + " LIMIT 1;"
    )
    rows = _run_booking_mysql_select(config, query)
    if not rows or not rows[0].strip():
        return None
    return rows[0].strip()


def _find_booking_outbox_event(
    config: LiveCheckConfig, *, appointment_id: str
) -> BookingOutboxEvent | None:
    """读取本轮预约创建事件，确认 Outbox 已由预约发布器标记为 PUBLISHED。"""

    query = (
        "SELECT event_key,event_type,aggregate_id,organization_id,status,payload "
        "FROM agent_booking_outbox WHERE aggregate_id = "
        + _sql_literal(appointment_id)
        + " AND event_type = 'APPOINTMENT_CREATED' ORDER BY id DESC LIMIT 1;"
    )
    rows = _run_booking_mysql_select(config, query)
    if not rows:
        return None
    fields = rows[0].split("\t", 5)
    if len(fields) != 6:
        raise ProactiveBookingLiveCheckError("预约 Outbox 查询结果字段不完整")
    try:
        payload = json.loads(fields[5])
    except ValueError as exc:
        raise ProactiveBookingLiveCheckError("预约 Outbox payload 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ProactiveBookingLiveCheckError("预约 Outbox payload 不是 JSON 对象")
    return BookingOutboxEvent(
        event_key=fields[0],
        event_type=fields[1],
        aggregate_id=fields[2],
        organization_id=fields[3],
        status=fields[4],
        payload=payload,
    )


async def _wait_for_booking_outbox(
    config: LiveCheckConfig, *, request_id: str
) -> BookingOutboxEvent:
    """等待业务操作和 Outbox 发布完成，避免只凭 Agent 下游结果判定成功。"""

    deadline = time.monotonic() + config.poll_timeout_seconds
    appointment_id: str | None = None
    last_status = "NOT_FOUND"
    while time.monotonic() < deadline:
        if appointment_id is None:
            appointment_id = await asyncio.to_thread(
                _find_booking_appointment_id, config, request_id
            )
        if appointment_id is not None:
            event = await asyncio.to_thread(
                _find_booking_outbox_event, config, appointment_id=appointment_id
            )
            if event is not None:
                last_status = event.status
                if event.status == "PUBLISHED":
                    return event
                if event.status == "DEAD":
                    raise ProactiveBookingLiveCheckError("预约 Outbox 已进入 DEAD")
        await asyncio.sleep(1)
    raise ProactiveBookingLiveCheckError(
        f"等待预约 Outbox 发布超时，appointment_id={appointment_id}, status={last_status}"
    )


def _event_routing_key(event_type: str) -> str:
    """与预约 Rabbit 发布器保持一致，用于受控重复投递验收。"""

    routing_keys = {
        "APPOINTMENT_CREATED": "appointment.created",
        "APPOINTMENT_RESCHEDULED": "appointment.rescheduled",
        "APPOINTMENT_CANCELLED": "appointment.cancelled",
    }
    try:
        return routing_keys[event_type]
    except KeyError as exc:
        raise ProactiveBookingLiveCheckError("不支持的预约 Outbox 事件类型") from exc


async def _republish_duplicate_event(config: LiveCheckConfig, event: BookingOutboxEvent) -> None:
    """把同一 Outbox 信封再次投递，验证 Agent Inbox 的 event_id 幂等。"""

    envelope = {
        "eventId": event.event_key,
        "source": "booking",
        "eventType": event.event_type,
        "aggregateId": event.aggregate_id,
        "organizationId": event.organization_id,
        "payload": event.payload,
    }
    connection = await aio_pika.connect_robust(config.rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            config.event_exchange, aio_pika.ExchangeType.DIRECT, durable=True
        )
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=_event_routing_key(event.event_type),
        )
    finally:
        await connection.close()


async def _probe_chain(
    connection: asyncpg.Connection,
    *,
    organization_id: str,
    event_id: str,
) -> ProactiveChainResult | None:
    """按预约 Outbox 的 event_id 查询下游状态，避免误读其他并发事件。"""

    event = await connection.fetchrow(
        """
        SELECT event_id, aggregate_id, status, payload
        FROM agent_proactive_event_inbox
        WHERE organization_id = $1
          AND event_type = 'APPOINTMENT_CREATED'
          AND event_id = $2
        ORDER BY created_at DESC, event_id DESC
        LIMIT 1
        """,
        organization_id,
        event_id,
    )
    if event is None:
        return None
    event_id = str(event["event_id"])
    payload = dict(event["payload"])
    expected_notification_count = len(
        {str(payload[key]) for key in ("studentId", "coachId") if payload.get(key)}
    )
    notification_rows = await connection.fetch(
        """
        SELECT status
        FROM agent_notification_outbox
        WHERE dedupe_key LIKE $1
        """,
        f"proactive:{event_id}:%",
    )
    published_count = sum(row["status"] == "PUBLISHED" for row in notification_rows)
    in_app_count = await connection.fetchval(
        """
        SELECT COUNT(*)
        FROM agent_in_app_notifications
        WHERE dedupe_key LIKE $1
        """,
        f"proactive:{event_id}:%",
    )
    return ProactiveChainResult(
        event_id=event_id,
        aggregate_id=str(event["aggregate_id"]),
        event_status=str(event["status"]),
        notification_outbox_count=len(notification_rows),
        published_notification_count=published_count,
        in_app_notification_count=int(in_app_count or 0),
        expected_notification_count=expected_notification_count,
    )


async def _wait_for_chain(config: LiveCheckConfig, *, event_id: str) -> ProactiveChainResult:
    """等待事件 Inbox、通知 Outbox 和站内收件箱全部达到终态。"""

    deadline = time.monotonic() + config.poll_timeout_seconds
    connection = await asyncpg.connect(config.database_url)
    last: ProactiveChainResult | None = None
    try:
        while time.monotonic() < deadline:
            last = await _probe_chain(
                connection,
                organization_id=config.organization_id,
                event_id=event_id,
            )
            if last is not None and (
                last.event_status == "PROCESSED"
                and last.expected_notification_count == 2
                and last.notification_outbox_count == last.expected_notification_count
                and last.published_notification_count == last.expected_notification_count
                and last.in_app_notification_count == last.expected_notification_count
            ):
                return last
            await asyncio.sleep(1)
    finally:
        await connection.close()
    if last is None:
        raise ProactiveBookingLiveCheckError("等待 Agent 事件 Inbox 超时，未收到预约创建事件")
    raise ProactiveBookingLiveCheckError(
        "主动提醒链路未完成："
        f"event_status={last.event_status}, "
        f"notification_outbox={last.notification_outbox_count}, "
        f"published={last.published_notification_count}, "
        f"in_app={last.in_app_notification_count}, "
        f"expected={last.expected_notification_count}"
    )


async def _assert_duplicate_does_not_grow(
    config: LiveCheckConfig, *, event_id: str, baseline: ProactiveChainResult
) -> None:
    """重复投递后轮询一小段时间，确认 Inbox 和通知数量没有增长。"""

    deadline = time.monotonic() + min(config.poll_timeout_seconds, 10.0)
    connection = await asyncpg.connect(config.database_url)
    try:
        while time.monotonic() < deadline:
            current = await _probe_chain(
                connection,
                organization_id=config.organization_id,
                event_id=event_id,
            )
            if current is not None and (
                current.notification_outbox_count > baseline.notification_outbox_count
                or current.in_app_notification_count > baseline.in_app_notification_count
            ):
                raise ProactiveBookingLiveCheckError("重复投递导致通知数量增长，event_id 幂等失效")
            await asyncio.sleep(1)
    finally:
        await connection.close()


async def run_live_check(config: LiveCheckConfig) -> None:
    """执行前置检查，并按需运行一次真实主动提醒验收。"""

    try:
        await _run_preflight(config)
    except (asyncpg.PostgresError, aio_pika.exceptions.AMQPError) as exc:
        raise ProactiveBookingLiveCheckError("PostgreSQL 或 RabbitMQ 前置检查失败") from exc
    if not config.execute:
        print("主动提醒真实链路前置检查通过（未执行真实预约写入）")
        print("如需验收完整链路，请确认使用本地测试数据后追加 --execute")
        return

    conversation_id = f"proactive-booking-live-{uuid.uuid4().hex}"
    request_id = f"proactive-booking-live-{uuid.uuid4().hex}"
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        payload = await _post_chat(
            client,
            config,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        confirmation_id = _require_confirmation(payload)
        current = await _get_confirmation(client, config, confirmation_id)
        if current.get("authorization_status") != "PENDING":
            raise ProactiveBookingLiveCheckError("确认单初始状态不是 PENDING")
        await _decide_confirmation(client, config, confirmation_id, request_id)
        await _wait_for_execution(client, config, confirmation_id)

    booking_event = await _wait_for_booking_outbox(config, request_id=request_id)
    result = await _wait_for_chain(config, event_id=booking_event.event_key)
    await _republish_duplicate_event(config, booking_event)
    await _assert_duplicate_does_not_grow(
        config,
        event_id=booking_event.event_key,
        baseline=result,
    )
    print("预约主动提醒真实链路验收通过")
    print(f"booking_outbox_event_id={booking_event.event_key}")
    print(f"event_id={result.event_id}")
    print(f"appointment_id={result.aggregate_id}")
    print(
        f"event_status={result.event_status}; "
        f"notification_outbox={result.notification_outbox_count}; "
        f"published={result.published_notification_count}; "
        f"in_app={result.in_app_notification_count}; "
        f"expected={result.expected_notification_count}"
    )
    print("重复投递验收通过：Inbox 和站内通知数量未增长")
    print("请使用已有取消预约确认流程清理本次本地测试预约；本脚本不会直接删除业务数据")


def main() -> int:
    """命令行入口，失败时返回非零状态供验收流水线使用。"""

    try:
        config = build_config(build_parser().parse_args())
        asyncio.run(run_live_check(config))
    except (
        ProactiveBookingLiveCheckError,
        asyncpg.PostgresError,
        aio_pika.exceptions.AMQPError,
        OSError,
        ValueError,
    ) as exc:
        print(f"预约主动提醒真实验收失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
