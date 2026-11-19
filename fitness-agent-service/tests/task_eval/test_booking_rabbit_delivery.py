from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.evaluation.task_eval import booking_rabbit_delivery as rabbit
from app.evaluation.task_eval.sandbox_preflight import PreflightError

RUN = "a" * 16
CONTAINER = "b" * 64
PASSWORD = "c" * 48


def container() -> dict[str, Any]:
    return {
        "Id": CONTAINER,
        "Name": f"/fitness-eval-rabbit-{RUN}",
        "State": {"Running": True},
        "Config": {"Image": "rabbitmq:3.13-management-alpine", "Labels": {"fitness.eval": RUN}},
        "HostConfig": {"AutoRemove": True, "Tmpfs": {"/var/lib/rabbitmq": ""}},
        "Mounts": [],
        "NetworkSettings": {
            "Ports": {
                "5672/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15872"}],
                "15672/tcp": None,
            }
        },
    }


def test_owned_loopback_container_accepted() -> None:
    assert rabbit.verify_container(container(), run_id=RUN) == 15872


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x["State"].update(Running=False),
        lambda x: x["Config"].update(Image="rabbitmq:latest"),
        lambda x: x["Config"]["Labels"].update({"fitness.eval": "other"}),
        lambda x: x["HostConfig"].update(Tmpfs={}),
        lambda x: x["HostConfig"].update(AutoRemove=False),
        lambda x: x["Mounts"].append({"Type": "bind"}),
        lambda x: x["NetworkSettings"]["Ports"]["5672/tcp"][0].update(HostIp="0.0.0.0"),
        lambda x: x["NetworkSettings"]["Ports"].update(
            {"15672/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1"}]}
        ),
    ],
)
def test_unowned_or_nonisolated_broker_rejected(change: Any) -> None:
    info = deepcopy(container())
    change(info)
    with pytest.raises(PreflightError):
        rabbit.verify_container(info, run_id=RUN)


async def test_unverified_broker_never_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    info = container()
    info["Config"]["Labels"]["fitness.eval"] = "other"
    monkeypatch.setattr(rabbit, "_inspect", lambda _: info)
    connection = AsyncMock()
    monkeypatch.setattr(rabbit.aio_pika, "connect", connection)
    fixture = rabbit.BrokerFixture(RUN, CONTAINER, PASSWORD)
    with pytest.raises(PreflightError):
        async with rabbit.BrokerDelivery(fixture, b"test"):
            pass
    connection.assert_not_awaited()


@pytest.mark.parametrize("operation", ["prepare", "receive_java"])
async def test_java_publisher_requires_verified_local_broker(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    info = container()
    info["NetworkSettings"]["Ports"]["5672/tcp"][0]["HostIp"] = "0.0.0.0"
    monkeypatch.setattr(rabbit, "_inspect", lambda _: info)
    connect = AsyncMock()
    monkeypatch.setattr(rabbit.aio_pika, "connect", connect)
    fixture = rabbit.BrokerFixture(RUN, CONTAINER, PASSWORD)
    with pytest.raises(PreflightError):
        if operation == "prepare":
            await rabbit.prepare_java_publisher(fixture)
        else:
            async with rabbit.JavaPublisherDelivery(fixture, b""):
                pass
    connect.assert_not_awaited()


def test_java_publisher_names_are_run_scoped():
    fixture = rabbit.BrokerFixture(RUN, CONTAINER, PASSWORD)
    assert fixture.exchange == f"fitness-eval-events-{RUN}"
    assert fixture.queue == f"fitness-eval-java-{RUN}"
    with pytest.raises(PreflightError):
        _ = rabbit.BrokerFixture("../other", CONTAINER, PASSWORD).queue
