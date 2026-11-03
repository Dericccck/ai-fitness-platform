"""Legacy checkpoints are not trusted unless the exact authorized turn committed."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.infrastructure.agent_context import AgentIdentity, conversation_thread_id
from app.streaming.control import run_owner
from app.streaming.legacy_bridge import LegacyConversationBridge, LegacyConversationUnavailable


def identity() -> AgentIdentity:
    return AgentIdentity("u1", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999)


class Saver:
    def __init__(self, values):
        self.values = values
        self.thread = None

    async def aget_tuple(self, config):
        self.thread = config["configurable"]["thread_id"]
        return SimpleNamespace(checkpoint={"channel_values": self.values})


class Runs:
    def __init__(self, expected, commit="run1"):
        self.expected = expected
        self.commit = commit

    async def history_commit(self, thread, owner, subject):
        assert (thread, owner, subject) == self.expected
        return SimpleNamespace(run_id=self.commit) if self.commit else None


async def test_only_committed_same_identity_and_memory_version_is_used():
    actor = identity()
    expected = (conversation_thread_id("old-chat", actor), run_owner(actor), actor.subject)
    values = {
        "turn_run_id": "run1",
        "memory_revision": 4,
        "messages": [
            {"role": "system", "content": "secret"},
            {"role": "user", "content": "深蹲"},
            {"role": "assistant", "content": "注意平衡"},
            {"role": "tool", "content": "private"},
        ],
    }
    saver = Saver(values)
    bridge = LegacyConversationBridge(saver, Runs(expected))
    assert await bridge.history(actor, "old-chat", memory_revision=4) == [
        {"role": "user", "content": "深蹲"},
        {"role": "assistant", "content": "注意平衡"},
    ]
    assert await bridge.history(actor, "old-chat", memory_revision=5) == []
    assert await bridge.history(actor, "old-chat", memory_revision=None) == []
    assert saver.thread == expected[0]
    bridge.runs.commit = "different"
    with pytest.raises(LegacyConversationUnavailable):
        await bridge.history(actor, "old-chat", memory_revision=4)
    bridge.runs.commit = "run1"
    saver.values = {**values, "request": "sensitive"}
    with pytest.raises(LegacyConversationUnavailable):
        await bridge.history(actor, "old-chat", memory_revision=4)
    assert conversation_thread_id("old-chat", replace(actor, subject="u2")) != expected[0]
