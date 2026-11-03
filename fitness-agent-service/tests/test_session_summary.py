from typing import Any

from app.confirmation.cipher import AesGcmPayloadCipher
from app.session_summary import (
    SessionSummaryRecord,
    SessionSummaryRepository,
    SessionSummaryService,
    _sanitize_summary,
    build_compacted_messages,
)


async def test_summary_invalidation_keeps_chat_history_commit() -> None:
    statements: list[str] = []

    class Connection:
        async def execute(self, query: Any, _: Any) -> Any:
            statements.append(str(query))
            return type("Result", (), {"rowcount": 1})()

    class Transaction:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_: object) -> None:
            return None

    class Engine:
        def begin(self) -> Transaction:
            return Transaction()

    database = type("Database", (), {"engine": Engine()})()
    assert await SessionSummaryRepository(database).invalidate_for_subject("user-1") == 1
    assert len(statements) == 1
    assert "DELETE FROM agent_session_summaries" in statements[0]
    assert "agent_chat_history_commits" not in statements[0]


class FakeModels:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        self.calls.append(messages)
        return self.response


class FakeRepository:
    def __init__(self) -> None:
        self.record: SessionSummaryRecord | None = None
        self.upsert_calls: list[dict[str, Any]] = []
        self.memory_revision = 0

    async def current_memory_revision(self, subject_user_id: str) -> int:
        return self.memory_revision

    async def publish(self, draft: Any) -> None:
        assert (self.record.summary_version if self.record else 0) == draft.base_version
        await self.upsert(
            thread_id=draft.thread_id,
            subject_user_id=draft.subject_user_id,
            summary_ciphertext=draft.summary_ciphertext,
            summary_key_version=draft.summary_key_version,
            summary_hash=draft.summary_hash,
            message_count=draft.message_count,
            retention_days=draft.retention_days,
        )

    async def get_for_subject(
        self, thread_id: str, subject_user_id: str
    ) -> SessionSummaryRecord | None:
        if self.record is not None and self.record.subject_user_id == subject_user_id:
            return self.record
        return None

    async def upsert(self, **kwargs: Any) -> SessionSummaryRecord:
        self.upsert_calls.append(kwargs)
        self.record = SessionSummaryRecord(
            thread_id=kwargs["thread_id"],
            subject_user_id=kwargs["subject_user_id"],
            summary_ciphertext=kwargs["summary_ciphertext"],
            summary_key_version=kwargs["summary_key_version"],
            summary_hash=kwargs["summary_hash"],
            summary_version=(self.record.summary_version + 1 if self.record else 1),
            message_count=kwargs["message_count"],
            retention_until=None,  # type: ignore[arg-type]
        )
        return self.record


def _messages(count: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index in range(count):
        result.append({"role": "user", "content": f"用户问题 {index}"})
        result.append({"role": "assistant", "content": f"助手回答 {index}"})
    return result


async def test_session_summary_is_encrypted_and_reused_only_after_incremental_threshold() -> None:
    repository = FakeRepository()
    models = FakeModels('{"summary":"用户想改善力量训练；动态课程信息需要重新查询。"}')
    service = SessionSummaryService(
        models,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        AesGcmPayloadCipher(b"x" * 32, "test-v1"),
        trigger_messages=4,
        keep_recent_messages=2,
    )

    summary = await service.maybe_summarize(
        thread_id="fitness:thread-1", subject_user_id="student-1", messages=_messages(2),
        memory_revision=0,
    )

    assert summary == "用户想改善力量训练；动态课程信息需要重新查询。"
    assert repository.upsert_calls[0]["summary_ciphertext"] != summary.encode()
    assert await service.load_for_subject("fitness:thread-1", "student-1") == summary
    assert (
        await service.maybe_summarize(
            thread_id="fitness:thread-1",
            subject_user_id="student-1",
            messages=_messages(2) + [{"role": "user", "content": "继续"}],
            memory_revision=0,
        )
        is None
    )
    assert len(models.calls) == 1


async def test_summary_skips_missing_or_stale_turn_memory_revision() -> None:
    repository = FakeRepository()
    repository.memory_revision = 2
    models = FakeModels('{"summary":"不应生成"}')
    service = SessionSummaryService(
        models,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        AesGcmPayloadCipher(b"x" * 32, "test-v1"),
        trigger_messages=2,
        keep_recent_messages=1,
    )
    for revision in (None, 1):
        assert await service.prepare_summary(
            thread_id="thread", subject_user_id="user", messages=_messages(2),
            memory_revision=revision,
        ) is None
    assert models.calls == []


def test_compacted_messages_remove_tool_messages_and_keep_summary_boundary() -> None:
    compacted = build_compacted_messages(
        system_prompt="系统规则",
        summary="用户目标是增肌；动态数据要重新查询。",
        previous_messages=[
            {"role": "system", "content": "旧系统提示"},
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "tool", "content": '{"private":"result"}'},
            {"role": "user", "content": "最新问题"},
        ],
        keep_recent_messages=3,
    )

    assert [message["role"] for message in compacted] == [
        "system",
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "旧系统提示" not in str(compacted)
    assert "动态数据要重新查询" in compacted[1]["content"]


def test_summary_sanitizer_removes_common_credentials() -> None:
    sanitized = _sanitize_summary(
        "用户提供 token: abcdefghijklmnop，目标是减脂；sk-abcdefghijklmnopqrstuvwxyz",
        3000,
    )

    assert "abcdefghijklmnop" not in sanitized
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "已脱敏" in sanitized


async def test_candidate_preparation_does_not_publish_and_stale_version_cannot_replace_summary() -> (
    None
):
    repository = FakeRepository()
    service = SessionSummaryService(
        FakeModels('{"summary":"候选摘要"}'),
        repository,
        AesGcmPayloadCipher(b"x" * 32, "test-v1"),
        trigger_messages=2,
        keep_recent_messages=1,
    )
    draft = await service.prepare_summary(
        thread_id="thread", subject_user_id="subject", messages=_messages(2), memory_revision=0,
    )
    assert draft is not None and not repository.upsert_calls
    await repository.publish(draft)
    first = repository.record
    import pytest

    with pytest.raises(AssertionError):
        await repository.publish(draft)
    assert repository.record == first
