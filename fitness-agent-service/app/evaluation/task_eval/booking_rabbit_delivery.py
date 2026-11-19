"""Transport duplicate events only through an owned, disposable local RabbitMQ.

This does not run the Java publisher and never uses the application's broker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Self, cast

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from .sandbox_preflight import PreflightError, _inspect

_RUN = re.compile(r"[0-9a-f]{16}\Z")
_CONTAINER = re.compile(r"[0-9a-f]{64}\Z")
_PASSWORD = re.compile(r"[0-9a-f]{48}\Z")


def verify_container(info: dict[str, Any], *, run_id: str) -> int:
    """Accept only the loopback binding of our tmpfs-backed task container."""
    if not _RUN.fullmatch(run_id):
        raise PreflightError("INVALID_RUN")
    try:
        config, host = info["Config"], info["HostConfig"]
        mounts, ports = info["Mounts"], info["NetworkSettings"]["Ports"]
        if (
            not _CONTAINER.fullmatch(info["Id"])
            or info["Name"] != f"/fitness-eval-rabbit-{run_id}"
            or info["State"]["Running"] is not True
            or config["Image"] != "rabbitmq:3.13-management-alpine"
            or (config.get("Labels") or {}).get("fitness.eval") != run_id
            or host["AutoRemove"] is not True
            or "/var/lib/rabbitmq" not in (host.get("Tmpfs") or {})
            or not isinstance(mounts, list)
            or any(mount.get("Type") != "tmpfs" for mount in mounts)
            or not isinstance(ports, dict)
            or "5672/tcp" not in ports
            or any(value is not None for key, value in ports.items() if key != "5672/tcp")
        ):
            raise PreflightError("CONTAINER_NOT_DISPOSABLE")
        bindings = ports["5672/tcp"]
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise PreflightError("INVALID_PORT_BINDING")
        if bindings[0]["HostIp"] != "127.0.0.1":
            raise PreflightError("INVALID_PORT_BINDING")
        port = int(bindings[0]["HostPort"])
        if not 1 <= port <= 65535:
            raise PreflightError("INVALID_PORT_BINDING")
        return port
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("INVALID_INSPECTION") from exc


@dataclass(frozen=True)
class BrokerFixture:
    run_id: str
    container_id: str
    password: str

    def verified_port(self) -> int:
        if not _PASSWORD.fullmatch(self.password):
            raise PreflightError("INVALID_BROKER_CREDENTIAL")
        return verify_container(_inspect(self.container_id), run_id=self.run_id)


class BrokerDelivery:
    """One exclusive queue in the verified fixture; both deliveries are actual AMQP."""

    def __init__(self, fixture: BrokerFixture, body: bytes) -> None:
        self.fixture = fixture
        self.body = body
        self.connection: Any = None
        self.channel: Any = None
        self.queue: Any = None

    async def __aenter__(self) -> Self:
        port = self.fixture.verified_port()
        self.connection = await aio_pika.connect(
            host="127.0.0.1",
            port=port,
            login="fitness_eval",
            password=self.fixture.password,
            virtualhost="/",
            timeout=5,
        )
        try:
            self.channel = await self.connection.channel(publisher_confirms=True)
            self.queue = await self.channel.declare_queue(
                f"fitness-eval-duplicate-{self.fixture.run_id}", exclusive=True, auto_delete=True
            )
        except BaseException:
            await self.connection.close()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.connection.close()

    async def receive(self) -> AbstractIncomingMessage:
        await self.channel.default_exchange.publish(
            aio_pika.Message(body=self.body),
            routing_key=self.queue.name,
            mandatory=True,
        )
        message = await self.queue.get(timeout=5, fail=True)
        if message is None or message.body != self.body or message.redelivered:
            raise ValueError("BROKER_DELIVERY_MISMATCH")
        return cast(AbstractIncomingMessage, message)

    async def empty(self) -> bool:
        # The exclusive queue should contain no unexpected third delivery.
        return await self.queue.get(timeout=5, fail=False) is None
