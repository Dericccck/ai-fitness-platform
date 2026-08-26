"""执行主动提醒 RabbitMQ/PostgreSQL 可靠性真实验收。

本脚本不调用 Booking、Training 或客服接口，也不写入 MySQL 业务事实。它只使用唯一的
临时 RabbitMQ 队列、事件 ID 和聚合 ID，验证 Agent 自己的事件 Inbox 与通知 Outbox：

1. 同一个事件通过真实 RabbitMQ 重复投递两次，最终只能有一条 Inbox 记录和两条通知 Outbox；
2. Worker 停止期间把一个临时事件留在 PostgreSQL Inbox，Worker 重启后能够继续处理；
3. 所有临时 PostgreSQL 数据和 RabbitMQ 队列在结束时按唯一 ID 清理。

默认只执行依赖前置检查；必须显式传入 ``--execute`` 才会启动临时 Worker 并产生测试数据。
真实 RabbitMQ 容器断电、PostgreSQL 容器重启和网络隔离属于需要人工观察的故障注入，不在
脚本中自动执行，避免脚本误操作用户正在使用的 Docker 服务。
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
from pathlib import Path
from subprocess import Popen
from typing import Any

import aio_pika
import asyncpg  # type: ignore[import-untyped]


class ProactiveReliabilityLiveCheckError(RuntimeError):
    """主动提醒真实可靠性验收未达到预期。"""


@dataclass(frozen=True)
class LiveCheckConfig:
    """真实验收使用的连接参数；敏感值只从环境变量或默认本地容器读取。"""

    rabbitmq_url: str
    database_url: str
    event_exchange: str
    routing_key: str
    organization_id: str
    poll_timeout_seconds: float
    execute: bool


@dataclass(frozen=True)
class ReliabilityEvent:
    """本轮临时事件的所有可清理标识。"""

    event_id: str
    aggregate_id: str
    organization_id: str
    student_id: str
    coach_id: str

    @property
    def payload(self) -> dict[str, str]:
        return {"studentId": self.student_id, "coachId": self.coach_id}


def build_parser() -> argparse.ArgumentParser:
    """构造参数；默认只读，避免把脚本误当成业务写入验收。"""

    parser = argparse.ArgumentParser(description="验收主动提醒 RabbitMQ/PostgreSQL 可靠性")
    parser.add_argument(
        "--rabbitmq-url",
        default=os.getenv(
            "AGENT_PROACTIVE_RABBITMQ_URL",
            "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/",
        ),
        help="RabbitMQ 地址，默认读取 AGENT_PROACTIVE_RABBITMQ_URL",
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
        "--event-exchange",
        default=os.getenv("AGENT_PROACTIVE_RABBITMQ_EXCHANGE", "fitness.domain.events"),
        help="主动提醒事件 Exchange 名称",
    )
    parser.add_argument(
        "--routing-key",
        default="appointment.created",
        help="测试事件路由键，默认 appointment.created",
    )
    parser.add_argument(
        "--organization-id",
        default=os.getenv("DEV_AGENT_ORG_ID", "reliability-live-organization"),
        help="临时事件所属机构，不代表真实业务机构权限",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "60")),
        help="等待 Worker 处理完成的最长时间，默认 60 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.getenv("AGENT_LIVE_EXECUTE_WRITES") == "1",
        help="启动临时 Worker 并执行真实 RabbitMQ/PostgreSQL 验收",
    )
    return parser


def build_config(args: argparse.Namespace) -> LiveCheckConfig:
    """校验非敏感运行参数，并统一 PostgreSQL URL 格式。"""

    rabbitmq_url = str(args.rabbitmq_url).strip()
    database_url = str(args.database_url).strip()
    if not rabbitmq_url or not database_url:
        raise ProactiveReliabilityLiveCheckError("RabbitMQ 和 PostgreSQL 地址不能为空")
    if not str(args.event_exchange).strip() or not str(args.routing_key).strip():
        raise ProactiveReliabilityLiveCheckError("Exchange 和 routing key 不能为空")
    if not str(args.organization_id).strip():
        raise ProactiveReliabilityLiveCheckError("organization_id 不能为空")
    if args.poll_timeout_seconds <= 0:
        raise ProactiveReliabilityLiveCheckError("poll-timeout-seconds 必须大于 0")
    return LiveCheckConfig(
        rabbitmq_url=rabbitmq_url,
        database_url=database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        event_exchange=str(args.event_exchange).strip(),
        routing_key=str(args.routing_key).strip(),
        organization_id=str(args.organization_id).strip(),
        poll_timeout_seconds=float(args.poll_timeout_seconds),
        execute=bool(args.execute),
    )


async def run_preflight(config: LiveCheckConfig) -> None:
    """只读检查两套基础设施和主动提醒表是否存在。"""

    connection = await asyncpg.connect(config.database_url)
    try:
        await connection.fetchval("SELECT 1")
        for table_name in ("agent_proactive_event_inbox", "agent_notification_outbox"):
            exists = await connection.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
            if exists is None:
                raise ProactiveReliabilityLiveCheckError(f"缺少 PostgreSQL 表：{table_name}")
    finally:
        await connection.close()

    rabbit_connection = await aio_pika.connect_robust(config.rabbitmq_url)
    await rabbit_connection.close()


def _new_event(config: LiveCheckConfig, *, suffix: str) -> ReliabilityEvent:
    """生成只包含测试标识的预约事件；学员和教练 ID 不会访问 Java 业务库。"""

    token = uuid.uuid4().hex
    return ReliabilityEvent(
        event_id=f"proactive-reliability:{suffix}:{token}",
        aggregate_id=f"reliability-appointment:{token}",
        organization_id=config.organization_id,
        student_id=f"reliability-student:{token}",
        coach_id=f"reliability-coach:{token}",
    )


def _envelope(event: ReliabilityEvent) -> dict[str, Any]:
    """生成与 Booking Outbox 相同字段命名的标准事件信封。"""

    return {
        "eventId": event.event_id,
        "source": "booking",
        "eventType": "APPOINTMENT_CREATED",
        "aggregateId": event.aggregate_id,
        "organizationId": event.organization_id,
        "payload": event.payload,
    }


async def _publish_duplicate(config: LiveCheckConfig, event: ReliabilityEvent) -> None:
    """向真实 Exchange 连续发布两次同一 event_id，模拟网络重试或发布端重复发送。"""

    connection = await aio_pika.connect_robust(config.rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            config.event_exchange, aio_pika.ExchangeType.DIRECT, durable=True
        )
        body = json.dumps(_envelope(event), ensure_ascii=False, separators=(",", ":")).encode()
        for _ in range(2):
            await exchange.publish(
                aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=config.routing_key,
            )
    finally:
        await connection.close()


async def _wait_for_queue(config: LiveCheckConfig, queue_name: str, process: Popen[Any]) -> None:
    """等待临时 Worker 声明队列；Worker 提前退出时立即报告而不是盲等超时。"""

    deadline = time.monotonic() + min(config.poll_timeout_seconds, 20.0)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ProactiveReliabilityLiveCheckError(
                f"临时 Proactive Worker 提前退出，exit_code={process.returncode}"
            )
        connection = await aio_pika.connect_robust(config.rabbitmq_url)
        try:
            channel = await connection.channel()
            try:
                await channel.declare_queue(queue_name, passive=True)
                return
            except aio_pika.exceptions.AMQPError:
                pass
        finally:
            await connection.close()
        await asyncio.sleep(0.2)
    raise ProactiveReliabilityLiveCheckError("等待临时 RabbitMQ 队列声明超时")


async def _wait_for_processed(
    config: LiveCheckConfig, event: ReliabilityEvent
) -> tuple[str, int, int]:
    """按唯一 event_id 等待 Inbox 完成，并验证通知 Outbox 数量没有因重复投递增长。"""

    deadline = time.monotonic() + config.poll_timeout_seconds
    connection = await asyncpg.connect(config.database_url)
    try:
        while time.monotonic() < deadline:
            inbox_status = await connection.fetchval(
                "SELECT status FROM agent_proactive_event_inbox WHERE event_id = $1",
                event.event_id,
            )
            outbox_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM agent_notification_outbox WHERE dedupe_key LIKE $1",
                    f"proactive:{event.event_id}:%",
                )
                or 0
            )
            inbox_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM agent_proactive_event_inbox WHERE event_id = $1",
                    event.event_id,
                )
                or 0
            )
            if inbox_status == "PROCESSED" and inbox_count == 1 and outbox_count == 2:
                return str(inbox_status), inbox_count, outbox_count
            await asyncio.sleep(0.5)
    finally:
        await connection.close()
    raise ProactiveReliabilityLiveCheckError(
        f"重复投递验收超时：inbox_status={inbox_status!r}, "
        f"inbox_count={inbox_count}, outbox_count={outbox_count}"
    )


async def _insert_pending_event(config: LiveCheckConfig, event: ReliabilityEvent) -> None:
    """在 Worker 停止期间写入临时 PENDING 事件，模拟消费进程重启后的恢复场景。"""

    connection = await asyncpg.connect(config.database_url)
    try:
        await connection.execute(
            """
            INSERT INTO agent_proactive_event_inbox (
                event_id, source, event_type, aggregate_id, organization_id, payload
            ) VALUES ($1, 'booking', 'APPOINTMENT_CREATED', $2, $3, $4::jsonb)
            """,
            event.event_id,
            event.aggregate_id,
            event.organization_id,
            json.dumps(event.payload, ensure_ascii=False),
        )
    finally:
        await connection.close()


async def _stop_worker(process: Popen[Any] | None) -> None:
    """优雅停止临时 Worker；异常时只结束本脚本自己创建的进程。"""

    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, 10)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait, 5)


def _start_worker(config: LiveCheckConfig, queue_name: str) -> Popen[Any]:
    """启动临时独立 Worker，使用唯一队列，绝不接管用户已有的共享消费队列。"""

    service_dir = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "AGENT_PROACTIVE_WORKER_ENABLED": "true",
        "AGENT_DATABASE_URL": "postgresql+asyncpg://"
        + config.database_url.removeprefix("postgresql://"),
        "AGENT_PROACTIVE_RABBITMQ_URL": config.rabbitmq_url,
        "AGENT_PROACTIVE_RABBITMQ_EXCHANGE": config.event_exchange,
        "AGENT_PROACTIVE_RABBITMQ_QUEUE": queue_name,
        "AGENT_PROACTIVE_RABBITMQ_ROUTING_KEY": config.routing_key,
        "AGENT_PROACTIVE_WORKER_BATCH_SIZE": "10",
        "AGENT_PROACTIVE_WORKER_POLL_SECONDS": "0.2",
        # 让临时 Worker 不与现有 Worker 的 Prometheus 端口冲突。
        "AGENT_PROACTIVE_WORKER_METRICS_PORT": str(20_000 + uuid.uuid4().int % 20_000),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "app.proactive_worker_main"],
        cwd=service_dir,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _delete_queue(config: LiveCheckConfig, queue_name: str) -> None:
    """只删除本轮唯一队列和死信队列，不 purge 用户共享队列。"""

    connection = await aio_pika.connect_robust(config.rabbitmq_url)
    try:
        channel = await connection.channel()
        for name in (queue_name, f"{queue_name}.dead"):
            try:
                queue = await channel.declare_queue(name, passive=True)
                await queue.delete(if_unused=False, if_empty=False)
            except aio_pika.exceptions.AMQPError:
                # 某个队列不存在时继续清理另一个队列，并保留原始验收错误。
                continue
    finally:
        await connection.close()


async def _cleanup_events(config: LiveCheckConfig, events: list[ReliabilityEvent]) -> None:
    """按通知收件箱、投递尝试、通知 Outbox、事件 Inbox 的依赖顺序清理临时数据。"""

    if not events:
        return
    event_ids = [event.event_id for event in events]
    connection = await asyncpg.connect(config.database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                """
                DELETE FROM agent_in_app_notifications
                WHERE dedupe_key LIKE ANY($1::text[])
                """,
                [f"proactive:{event_id}:%" for event_id in event_ids],
            )
            await connection.execute(
                """
                DELETE FROM agent_notification_delivery_attempts
                WHERE outbox_id IN (
                    SELECT id FROM agent_notification_outbox
                    WHERE dedupe_key LIKE ANY($1::text[])
                )
                """,
                [f"proactive:{event_id}:%" for event_id in event_ids],
            )
            await connection.execute(
                """
                DELETE FROM agent_notification_outbox
                WHERE dedupe_key LIKE ANY($1::text[])
                """,
                [f"proactive:{event_id}:%" for event_id in event_ids],
            )
            await connection.execute(
                "DELETE FROM agent_proactive_event_inbox WHERE event_id = ANY($1::text[])",
                event_ids,
            )
    finally:
        await connection.close()


async def run_live_check(config: LiveCheckConfig) -> None:
    """运行前置检查和可选的真实重复投递/重启恢复验收。"""

    await run_preflight(config)
    if not config.execute:
        print("主动提醒可靠性前置检查通过（未启动临时 Worker，未写入测试数据）")
        print("如需执行真实 RabbitMQ/PostgreSQL 验收，请追加 --execute")
        return

    queue_name = f"fitness.proactive.reliability.{uuid.uuid4().hex}"
    duplicate_event = _new_event(config, suffix="duplicate")
    restart_event = _new_event(config, suffix="restart")
    events = [duplicate_event, restart_event]
    worker: Popen[Any] | None = None
    try:
        worker = _start_worker(config, queue_name)
        await _wait_for_queue(config, queue_name, worker)
        await _publish_duplicate(config, duplicate_event)
        duplicate_result = await _wait_for_processed(config, duplicate_event)

        # 先停止临时 Worker，再写入 PENDING，确保该事件只能由重启后的 Worker 处理。
        await _stop_worker(worker)
        worker = None
        await _insert_pending_event(config, restart_event)
        worker = _start_worker(config, queue_name)
        await _wait_for_queue(config, queue_name, worker)
        restart_result = await _wait_for_processed(config, restart_event)

        print("主动提醒真实可靠性验收通过")
        print(
            f"duplicate_event={duplicate_event.event_id}; "
            f"inbox={duplicate_result[1]}; notification_outbox={duplicate_result[2]}"
        )
        print(
            f"restart_event={restart_event.event_id}; "
            f"inbox={restart_result[1]}; notification_outbox={restart_result[2]}"
        )
    finally:
        await _stop_worker(worker)
        try:
            await _cleanup_events(config, events)
        finally:
            await _delete_queue(config, queue_name)


def main() -> int:
    """命令行入口；失败只返回非零，不打印连接串中的密码。"""

    try:
        args = build_parser().parse_args()
        asyncio.run(run_live_check(build_config(args)))
    except (
        ProactiveReliabilityLiveCheckError,
        asyncpg.PostgresError,
        aio_pika.exceptions.AMQPError,
        OSError,
    ) as exc:
        print(f"主动提醒可靠性验收失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
