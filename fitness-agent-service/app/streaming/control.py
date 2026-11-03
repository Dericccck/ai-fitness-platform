"""请求内取消信号；通过 ContextVar 传给本轮图节点，不进入 checkpoint。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.infrastructure.agent_context import AgentIdentity
from app.streaming.completion import TurnCompletion
from app.streaming.repository import ChatRunRepository, request_digest


def run_owner(identity: AgentIdentity) -> str:
    return request_digest(
        {
            "subject": identity.subject,
            "organizations": sorted(identity.organization_ids),
            "roles": sorted(identity.roles),
            "capabilities": sorted(identity.capabilities),
            "qualifications": sorted(identity.qualifications),
        }
    )


@dataclass
class RunControl:
    run_id: str
    owner: str
    repository: ChatRunRepository
    cancelled: bool = False
    parent: RunControl | None = None
    completion: TurnCompletion | None = None

    def child(self) -> RunControl:
        """父信号向子信号传播；单独取消子信号不改变父运行。"""
        return RunControl(self.run_id, self.owner, self.repository, parent=self)

    def check(self) -> None:
        if self.parent is not None:
            self.parent.check()
        if self.cancelled:
            raise asyncio.CancelledError("run stopped")

    async def checkpoint(self) -> None:
        self.check()
        run = await self.repository.get(self.run_id, self.owner)
        if run is None or run.status != "RUNNING":
            self.cancelled = True
        self.check()


_control: ContextVar[RunControl | None] = ContextVar("chat_run_control", default=None)


def current_control() -> RunControl | None:
    return _control.get()


@contextmanager
def run_control(control: RunControl) -> Iterator[None]:
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)


async def cancellation_checkpoint() -> None:
    control = current_control()
    if control is not None:
        await control.checkpoint()


def check_cancelled() -> None:
    control = current_control()
    if control is not None:
        control.check()
