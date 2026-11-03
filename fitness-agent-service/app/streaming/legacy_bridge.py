"""Read committed legacy conversation context without copying checkpoint data."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.agent_context import AgentIdentity, conversation_thread_id
from app.streaming.control import run_owner
from app.streaming.repository import ChatRunRepository


class LegacyConversationUnavailable(RuntimeError):
    """历史不存在、未提交、已过期或不属于当前授权身份。"""


class LegacyConversationBridge:
    def __init__(self, saver: Any, runs: ChatRunRepository) -> None:
        self.saver = saver
        self.runs = runs

    async def history(
        self, identity: AgentIdentity, conversation_id: str, *, memory_revision: int | None
    ) -> list[dict[str, str]]:
        thread = conversation_thread_id(conversation_id, identity)
        return await self.history_by_thread(identity, thread, memory_revision=memory_revision)

    async def history_by_thread(
        self,
        identity: AgentIdentity,
        thread: str,
        *,
        memory_revision: int | None,
        connection: AsyncConnection | None = None,
    ) -> list[dict[str, str]]:
        if not re.fullmatch(r"fitness:[0-9a-f]{64}", thread):
            raise LegacyConversationUnavailable("无效历史对话")
        # Check ownership before loading the checkpoint; thread IDs are opaque, not authorization.
        if connection is None:
            commit = await self.runs.history_commit(thread, run_owner(identity), identity.subject)
            committed_run = commit.run_id if commit else None
        else:
            committed_run = (
                await connection.execute(
                    text("""SELECT run_id FROM agent_chat_history_commits
                WHERE thread_id=:thread AND owner_hash=:owner AND subject_user_id=:subject"""),
                    {"thread": thread, "owner": run_owner(identity), "subject": identity.subject},
                )
            ).scalar_one_or_none()
        if committed_run is None:
            raise LegacyConversationUnavailable("未找到可安全关联的历史对话")
        raw = await self.saver.aget_tuple({"configurable": {"thread_id": thread}})
        checkpoint = getattr(raw, "checkpoint", None)
        values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
        if not isinstance(values, dict) or "request" in values:
            raise LegacyConversationUnavailable("未找到可安全关联的历史对话")
        run_id = values.get("turn_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise LegacyConversationUnavailable("历史对话没有已确认的完成记录")
        if committed_run != run_id:
            raise LegacyConversationUnavailable("历史对话没有已确认的完成记录")
        if memory_revision is None or values.get("memory_revision") != memory_revision:
            return []
        messages = values.get("messages")
        if not isinstance(messages, list):
            return []
        return [
            {"role": message["role"], "content": message["content"][:200]}
            for message in messages[-20:]
            if isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            and not message.get("tool_calls")
            and not message.get("function_call")
        ][-6:]
