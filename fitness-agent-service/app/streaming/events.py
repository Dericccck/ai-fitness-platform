from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langgraph.config import get_stream_writer

_enabled: ContextVar[bool] = ContextVar("answer_stream_enabled", default=False)


@contextmanager
def stream_events() -> Iterator[None]:
    token = _enabled.set(True)
    try:
        yield
    finally:
        _enabled.reset(token)


def streaming_enabled() -> bool:
    return _enabled.get()


def emit_event(event: str, data: dict[str, Any]) -> None:
    if _enabled.get():
        get_stream_writer()({"event": event, "data": data})


class AnswerFilter:
    """跨网络分片识别内部协议标记；保留可能的标记前缀，不提前输出。"""

    markers = ("<｜｜DSML｜｜tool_calls>", "<|DSML|tool_calls>", "<tool_calls>", "<think>")

    def __init__(self) -> None:
        self.pending = ""
        self.stopped = False

    def push(self, value: str, *, final: bool = False) -> str:
        if self.stopped:
            return ""
        self.pending += value
        positions = [self.pending.find(m) for m in self.markers if m in self.pending]
        if positions:
            value = self.pending[: min(positions)]
            self.pending = ""
            self.stopped = True
            return value
        held = 0
        if not final:
            for marker in self.markers:
                for size in range(1, len(marker)):
                    if self.pending.endswith(marker[:size]):
                        held = max(held, size)
        value = self.pending[:-held] if held else self.pending
        self.pending = self.pending[-held:] if held else ""
        return value
