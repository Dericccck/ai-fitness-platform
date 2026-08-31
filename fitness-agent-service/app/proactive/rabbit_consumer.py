"""RabbitMQ 主动提醒事件接收器。"""

from __future__ import annotations

import asyncio
from typing import cast

import aio_pika
import structlog
from aio_pika import ExchangeType
from aio_pika.abc import AbstractIncomingMessage

from app.infrastructure.database import Database

from .events import ProactiveEventContractError, ProactiveEventMessage
from .repository import ProactiveEventRepository

_logger = structlog.get_logger("proactive.rabbit_consumer")


def reconnect_delay(attempt: int, *, initial_seconds: float, max_seconds: float) -> float:
    """计算 RabbitMQ 重连退避时间。

    网络故障时不能让 Worker 忙循环连接 RabbitMQ，否则会放大 Broker 和网络压力；
    这里采用有上限的指数退避。attempt 从 1 开始，调用方可以把重连次数写入
    结构化日志，但不能把机构 ID 或用户 ID 放进日志字段。
    """

    if attempt < 1:
        raise ValueError("attempt 必须大于零")
    if initial_seconds <= 0 or max_seconds <= 0:
        raise ValueError("重连延迟必须大于零")
    if initial_seconds > max_seconds:
        raise ValueError("初始重连延迟不能超过最大延迟")
    # 30 次之后已经达到常见的最大退避范围，限制指数位数也避免异常长时间故障
    # 导致无界整数增长；最终等待时间仍由 max_seconds 统一封顶。
    return cast(float, min(max_seconds, initial_seconds * (2 ** min(attempt - 1, 30))))


class ProactiveRabbitConsumer:
    """把 RabbitMQ 事件可靠落到 Agent PostgreSQL Inbox。

    消息只有在 Inbox 事务提交后才确认；重复 event_id 即使再次投递也会安全确认。契约非法
    的消息进入 RabbitMQ dead-letter 队列，不会无限重试阻塞后续合法事件。
    """

    def __init__(
        self,
        database: Database,
        repository: ProactiveEventRepository,
        *,
        url: str,
        exchange_name: str,
        queue_name: str,
        routing_key: str,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        self.database = database
        self.repository = repository
        self.url = url
        self.exchange_name = exchange_name
        self.queue_name = queue_name
        self.routing_key = routing_key
        if reconnect_initial_seconds <= 0:
            raise ValueError("reconnect_initial_seconds 必须大于零")
        if reconnect_max_seconds < reconnect_initial_seconds:
            raise ValueError("reconnect_max_seconds 不能小于初始延迟")
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds

    async def run_forever(self) -> None:
        """持续消费并在初始连接或消费循环失败后自动重连。

        ``connect_robust`` 能恢复已经建立的连接，但初次连接失败、拓扑声明失败或
        消费循环因未确认消息异常退出时，仍需要由应用层重新创建连接和 Channel。
        外层循环保证 Worker 不会因为一次 RabbitMQ 故障永久退出；消息只有在 Inbox
        事务提交后才 ACK，因此重连后仍会重新投递未确认消息。
        """

        attempt = 0
        while True:
            try:
                await self._consume_connection()
                # 正常返回通常意味着连接被主动关闭；下一轮仍重新建立连接。
                attempt = 0
            except asyncio.CancelledError:
                raise
            # 消费循环中的数据库事务异常也必须进入重连路径，否则未 ACK 消息会留在
            # Broker 中而消费任务已经永久退出。CancelledError 已在上方单独放行。
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                delay = reconnect_delay(
                    attempt,
                    initial_seconds=self.reconnect_initial_seconds,
                    max_seconds=self.reconnect_max_seconds,
                )
                _logger.warning(
                    "proactive_rabbitmq_reconnect_scheduled",
                    attempt=attempt,
                    delay_seconds=delay,
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(delay)

    async def _consume_connection(self) -> None:
        """创建一次连接并声明拓扑；该方法失败后由外层负责退避重连。"""

        connection = await aio_pika.connect_robust(self.url)
        try:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=50)
            exchange = await channel.declare_exchange(
                self.exchange_name, ExchangeType.DIRECT, durable=True
            )
            dead_exchange = await channel.declare_exchange(
                f"{self.exchange_name}.dlx", ExchangeType.TOPIC, durable=True
            )
            dead_queue = await channel.declare_queue(
                f"{self.queue_name}.dead",
                durable=True,
            )
            await dead_queue.bind(dead_exchange, routing_key="#")
            queue = await channel.declare_queue(
                self.queue_name,
                durable=True,
                arguments={"x-dead-letter-exchange": dead_exchange.name},
            )
            for routing_key in self.routing_key.split(","):
                if routing_key.strip():
                    await queue.bind(exchange, routing_key=routing_key.strip())
            async with queue.iterator() as messages:
                async for message in messages:
                    await self._consume_one(message)
        finally:
            await connection.close()

    async def _consume_one(self, message: AbstractIncomingMessage) -> None:
        try:
            event = ProactiveEventMessage.from_json(message.body)
        except ProactiveEventContractError:
            _logger.exception("proactive_event_contract_rejected")
            await message.reject(requeue=False)
            return
        async with self.database.engine.begin() as connection:
            accepted = await self.repository.accept(connection, event=event)
        await message.ack()
        _logger.info(
            "proactive_event_accepted",
            event_id=event.event_id,
            event_type=event.event_type,
            duplicate=not accepted,
        )
