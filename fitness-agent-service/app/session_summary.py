"""短期会话摘要：压缩当前对话上下文，但不把内容升级为长期 Memory。

短期摘要的边界与长期 Memory 不同：它只服务于同一个 ``thread_id`` 的上下文压缩，
默认保留 7 天；它不能作为权限依据、业务事实或训练计划来源，也不会被 Memory
候选提取器自动升级。摘要正文在 PostgreSQL 中使用 AES-GCM 加密，线程标识和主体
范围仍以结构化字段保存，便于做隔离查询和生命周期清理。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import JsonModelTurn, ModelGateway

_logger = structlog.get_logger("agent.session_summary")


class SessionSummaryError(RuntimeError):
    """会话摘要不能安全读取、生成或保存。"""


class SessionSummaryScopeError(SessionSummaryError):
    """同一个 thread_id 被不同主体占用，拒绝覆盖，防止会话串线。"""


@dataclass(frozen=True)
class SessionSummaryRecord:
    """数据库中的摘要元数据；summary_ciphertext 只在服务内部短暂解密。"""

    thread_id: str
    subject_user_id: str
    summary_ciphertext: bytes
    summary_key_version: str
    summary_hash: str
    summary_version: int
    message_count: int
    retention_until: datetime


class SessionSummaryPayload(BaseModel):
    """限制模型输出为一个可审计的短文本对象，而不是任意 JSON。"""

    summary: str = Field(min_length=1, max_length=4000)


class SessionSummaryRepository:
    """会话摘要仓储。

    查询必须同时带 ``thread_id`` 和签名主体，不能因为客户端复用会话 ID 就读取
    另一个用户的摘要。写入时还会在数据库层检查 thread 与主体的绑定关系，避免
    仅依赖应用层判断造成竞态覆盖。
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_for_subject(
        self, thread_id: str, subject_user_id: str
    ) -> SessionSummaryRecord | None:
        statement = text(
            """
            SELECT thread_id, subject_user_id, summary_ciphertext, summary_key_version,
                   summary_hash, summary_version, message_count, retention_until
            FROM agent_session_summaries
            WHERE thread_id = :thread_id AND subject_user_id = :subject_user_id
            """
        )
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement, {"thread_id": thread_id, "subject_user_id": subject_user_id}
                    )
                )
                .mappings()
                .first()
            )
        return _record_from_row(row) if row is not None else None

    async def upsert(
        self,
        *,
        thread_id: str,
        subject_user_id: str,
        summary_ciphertext: bytes,
        summary_key_version: str,
        summary_hash: str,
        message_count: int,
        retention_days: int,
    ) -> SessionSummaryRecord:
        """保存摘要并递增版本；跨主体冲突时整次写入失败。"""

        statement = text(
            """
            INSERT INTO agent_session_summaries (
                thread_id, subject_user_id, summary_ciphertext, summary_key_version,
                summary_hash, summary_version, message_count, retention_until
            ) VALUES (
                :thread_id, :subject_user_id, :summary_ciphertext, :summary_key_version,
                :summary_hash, 1, :message_count,
                CURRENT_TIMESTAMP + (:retention_days * INTERVAL '1 day')
            )
            ON CONFLICT (thread_id) DO UPDATE SET
                summary_ciphertext = EXCLUDED.summary_ciphertext,
                summary_key_version = EXCLUDED.summary_key_version,
                summary_hash = EXCLUDED.summary_hash,
                summary_version = agent_session_summaries.summary_version + 1,
                message_count = EXCLUDED.message_count,
                retention_until = EXCLUDED.retention_until,
                updated_at = CURRENT_TIMESTAMP
            WHERE agent_session_summaries.subject_user_id = EXCLUDED.subject_user_id
            RETURNING thread_id, subject_user_id, summary_ciphertext, summary_key_version,
                      summary_hash, summary_version, message_count, retention_until
            """
        )
        async with self._database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "thread_id": thread_id,
                            "subject_user_id": subject_user_id,
                            "summary_ciphertext": summary_ciphertext,
                            "summary_key_version": summary_key_version,
                            "summary_hash": summary_hash,
                            "message_count": message_count,
                            "retention_days": retention_days,
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise SessionSummaryScopeError("session thread is already bound to another subject")
        return _record_from_row(row)

    async def delete_due(self, *, limit: int) -> int:
        """删除已过保留期限的摘要，使用 SKIP LOCKED 支持多个清理实例并行。"""

        statement = text(
            """
            WITH due AS (
                SELECT thread_id
                FROM agent_session_summaries
                WHERE retention_until <= CURRENT_TIMESTAMP
                ORDER BY retention_until, thread_id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            DELETE FROM agent_session_summaries AS target
            USING due
            WHERE target.thread_id = due.thread_id
            """
        )
        async with self._database.engine.begin() as connection:
            result = await connection.execute(statement, {"limit": limit})
        return result.rowcount or 0


class SessionSummaryService:
    """生成、解密和压缩短期会话摘要。"""

    def __init__(
        self,
        models: ModelGateway,
        repository: SessionSummaryRepository,
        cipher: AesGcmPayloadCipher,
        *,
        trigger_messages: int = 12,
        keep_recent_messages: int = 6,
        max_summary_chars: int = 3000,
        max_input_chars: int = 12_000,
        retention_days: int = 7,
        metrics: HttpMetrics | None = None,
    ) -> None:
        if trigger_messages < 2 or keep_recent_messages < 1:
            raise ValueError("invalid session summary message thresholds")
        if keep_recent_messages >= trigger_messages:
            raise ValueError("keep_recent_messages must be smaller than trigger_messages")
        self.models = models
        self.repository = repository
        self.cipher = cipher
        self.trigger_messages = trigger_messages
        self.keep_recent_messages = keep_recent_messages
        self.max_summary_chars = max_summary_chars
        self.max_input_chars = max_input_chars
        self.retention_days = retention_days
        self.metrics = metrics

    async def load_for_subject(self, thread_id: str, subject_user_id: str) -> str | None:
        """解密摘要；密钥版本或完整性不匹配时拒绝使用，而不是猜测内容。"""

        record = await self.repository.get_for_subject(thread_id, subject_user_id)
        if record is None:
            return None
        if record.summary_key_version != self.cipher.key_version:
            _logger.warning("session_summary_key_version_mismatch", thread_id=thread_id)
            return None
        try:
            plaintext = self.cipher.decrypt(
                record.summary_ciphertext,
                associated_data=_associated_data(thread_id, record.summary_hash),
            ).decode("utf-8")
        except (ConfirmationPayloadCipherError, UnicodeDecodeError):
            _logger.warning("session_summary_decrypt_failed", thread_id=thread_id)
            return None
        if _sha256(plaintext) != record.summary_hash:
            _logger.warning("session_summary_hash_mismatch", thread_id=thread_id)
            return None
        return plaintext

    async def maybe_summarize(
        self,
        *,
        thread_id: str,
        subject_user_id: str,
        messages: list[dict[str, Any]],
    ) -> str | None:
        """达到增量阈值后生成摘要；摘要失败不阻断本轮已完成的回答。"""

        conversation_messages = _conversation_messages(messages)
        existing_record = await self.repository.get_for_subject(thread_id, subject_user_id)
        previous_count = existing_record.message_count if existing_record else 0
        if len(conversation_messages) < self.trigger_messages or (
            existing_record is not None
            and len(conversation_messages) - previous_count < self.keep_recent_messages
        ):
            self._record_event("skipped_threshold")
            return None

        self._record_event("triggered")

        existing_summary = None
        if existing_record is not None:
            existing_summary = await self.load_for_subject(thread_id, subject_user_id)
        source = _summary_source(
            conversation_messages,
            existing_summary=existing_summary,
            max_chars=self.max_input_chars,
        )
        try:
            model_turn = await self._chat_json_with_usage(source)
            self._record_tokens(model_turn)
            self._record_chars("input", len(source))
            raw = model_turn.content
        except Exception:
            self._record_event("failed")
            raise
        try:
            payload = SessionSummaryPayload.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            self._record_event("failed")
            raise SessionSummaryError("LLM returned an invalid session summary") from exc
        raw_summary = payload.summary.strip()
        summary = _sanitize_summary(raw_summary, self.max_summary_chars)
        if summary != raw_summary:
            self._record_event("redacted")
        if not summary:
            self._record_event("empty")
            return None
        self._record_chars("output", len(summary))
        summary_hash = _sha256(summary)
        ciphertext = self.cipher.encrypt(
            summary.encode("utf-8"),
            associated_data=_associated_data(thread_id, summary_hash),
        )
        try:
            await self.repository.upsert(
                thread_id=thread_id,
                subject_user_id=subject_user_id,
                summary_ciphertext=ciphertext,
                summary_key_version=self.cipher.key_version,
                summary_hash=summary_hash,
                message_count=len(conversation_messages),
                retention_days=self.retention_days,
            )
        except Exception:
            self._record_event("failed")
            raise
        self._record_event("stored")
        return summary

    async def _chat_json_with_usage(self, source: str) -> JsonModelTurn:
        """调用支持用量回传的网关；兼容只实现 chat_json 的测试替身。"""

        messages = [
            {
                "role": "system",
                "content": (
                    "你是健身平台的会话压缩器。只总结当前会话中对后续对话有帮助的上下文，"
                    "不要生成长期用户画像、医疗诊断、权限结论或训练计划事实。动态业务数据可能已过期，"
                    "必须标记为‘需要重新查询’。不要保留密码、Token、签名上下文、确认单 ID、密钥或其他凭证。"
                    '只返回 JSON：{"summary": "不超过指定长度的中文摘要"}。'
                ),
            },
            {
                "role": "user",
                "content": f"请压缩以下会话上下文，摘要最多 {self.max_summary_chars} 个字符：\n{source}",
            },
        ]
        method = getattr(self.models, "chat_json_with_usage", None)
        if method is not None:
            return cast(JsonModelTurn, await method(messages, temperature=0.1))
        return JsonModelTurn(content=await self.models.chat_json(messages, temperature=0.1))

    def _record_event(self, event: str) -> None:
        if self.metrics is not None:
            self.metrics.record_session_summary_event(event)

    def _record_tokens(self, turn: JsonModelTurn) -> None:
        if self.metrics is None:
            return
        if turn.input_tokens:
            self.metrics.session_summary_tokens_total.labels(direction="input").inc(
                turn.input_tokens
            )
        if turn.output_tokens:
            self.metrics.session_summary_tokens_total.labels(direction="output").inc(
                turn.output_tokens
            )

    def _record_chars(self, kind: str, count: int) -> None:
        if self.metrics is not None:
            self.metrics.session_summary_chars.labels(kind=kind).observe(count)


def build_compacted_messages(
    *,
    system_prompt: str,
    summary: str,
    previous_messages: list[dict[str, Any]],
    keep_recent_messages: int,
) -> list[dict[str, str]]:
    """构造下一轮模型上下文：当前系统规则、短期摘要和最近用户/助手消息。"""

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": (
                "【当前会话短期摘要】\n"
                f"{summary}\n"
                "该摘要仅用于保持当前会话连续性，不是长期 Memory、权限依据或最新业务事实；"
                "涉及课程、预约、训练计划等动态内容时必须重新调用工具查询。"
            ),
        },
    ]
    messages.extend(_recent_model_messages(previous_messages, keep_recent_messages))
    return messages


def _conversation_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        # 工具参数可能包含手机号、业务 ID 或确认参数；摘要只接收工具返回内容，
        # 不把 assistant tool_calls 原始结构交给模型或持久化层。
        result.append({"role": role, "content": content.strip()})
    return result


def _recent_model_messages(
    messages: list[dict[str, Any]], keep_recent_messages: int
) -> list[dict[str, str]]:
    """只保留 user/assistant，避免留下没有对应 assistant tool_call 的 tool 消息。"""

    conversations = [
        {"role": str(item["role"]), "content": str(item["content"])}
        for item in _conversation_messages(messages)
        if item["role"] in {"user", "assistant"}
    ]
    return conversations[-keep_recent_messages:]


def _summary_source(
    messages: list[dict[str, str]], *, existing_summary: str | None, max_chars: int
) -> str:
    parts: list[str] = []
    if existing_summary:
        parts.append(f"已有摘要（仅供更新，不代表最新事实）：\n{existing_summary}")
    parts.append(
        "会话消息：\n" + "\n".join(f"{item['role']}: {item['content']}" for item in messages)
    )
    text_value = "\n\n".join(parts)
    return text_value[-max_chars:]


def _sanitize_summary(value: str, max_chars: int) -> str:
    text_value = value.strip()
    # 这是确定性最后一道脱敏，不替代 Secret Scanner。它覆盖常见凭证键名，
    # 防止模型把用户误贴的 Authorization、API Key 或确认单标识带入摘要。
    text_value = re.sub(
        r"(?i)(x-agent-context|authorization|api[_ -]?key|password|secret|token|confirmation[_ -]?id)"
        r"\s*[:=：]\s*[^\s，。；;]+",
        r"\1=[已脱敏]",
        text_value,
    )
    text_value = re.sub(r"\b(?:sk-|ghp_|gsk_)[A-Za-z0-9_-]{16,}\b", "[已脱敏凭证]", text_value)
    return text_value[:max_chars].strip()


def _record_from_row(row: Any) -> SessionSummaryRecord:
    return SessionSummaryRecord(
        thread_id=str(row["thread_id"]),
        subject_user_id=str(row["subject_user_id"]),
        summary_ciphertext=bytes(row["summary_ciphertext"]),
        summary_key_version=str(row["summary_key_version"]),
        summary_hash=str(row["summary_hash"]),
        summary_version=int(row["summary_version"]),
        message_count=int(row["message_count"]),
        retention_until=row["retention_until"],
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _associated_data(thread_id: str, summary_hash: str) -> str:
    return f"fitness-session-summary:{thread_id}:{summary_hash}"
