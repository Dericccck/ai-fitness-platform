"""RabbitMQ 主动提醒事件接收器。"""

from __future__ import annotations

import aio_pika
import structlog
from aio_pika import ExchangeType
from aio_pika.abc import AbstractIncomingMessage

from app.infrastructure.database import Database

from .events import ProactiveEventContractError, ProactiveEventMessage
from .repository import ProactiveEventRepository

_logger = structlog.get_logger("proactive.rabbit_consumer")


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
    ) -> None:
        self.database = database
        self.repository = repository
        self.url = url
        self.exchange_name = exchange_name
        self.queue_name = queue_name
        self.routing_key = routing_key

    async def run_forever(self) -> None:
        """声明 Direct Exchange 拓扑并持续消费；连接断开由 robust connection 自动恢复。"""

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
