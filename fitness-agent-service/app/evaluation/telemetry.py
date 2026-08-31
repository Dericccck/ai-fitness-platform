"""注重隐私的 TruLens/OpenTelemetry 集成。

应用已经拥有进程级 OpenTelemetry provider。本模块只创建子 span 并发出 TruLens 语义属性；
不会安装第二个 provider，也不会将凭证、签名上下文、确认 Payload 或原始工具参数放入 span。
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span
from trulens.otel.semconv.trace import ResourceAttributes as TruLensResourceAttributes
from trulens.otel.semconv.trace import SpanAttributes as TruLensSpanAttributes

from app.core.config import Settings

_TRACER_NAME = "fitness-agent-service.trulens"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|authorization)\s*[=:]\s*)[^\s,;]+"
)

# TruLens 官方 OTEL 语义约定。直接使用官方常量，避免导出器升级后字符串漂移。
RECORD_INPUT = "ai.observability.record_root.input"
RECORD_OUTPUT = "ai.observability.record_root.output"
RETRIEVAL_QUERY = "ai.observability.retrieval.query_text"
RETRIEVAL_CONTEXTS = "ai.observability.retrieval.retrieved_contexts"
RETRIEVAL_COUNT = "ai.observability.retrieval.num_contexts"
TRULENS_APP_ID = TruLensResourceAttributes.APP_ID
TRULENS_APP_NAME = TruLensResourceAttributes.APP_NAME
TRULENS_APP_VERSION = TruLensResourceAttributes.APP_VERSION
TRULENS_SPAN_TYPE = TruLensSpanAttributes.SPAN_TYPE
TRULENS_RECORD_ID = TruLensSpanAttributes.RECORD_ID
TRULENS_CONVERSATION_ID = TruLensSpanAttributes.CONVERSATION_ID

_CURRENT_RECORD_ID: ContextVar[str | None] = ContextVar(
    "fitness_agent_trulens_record_id", default=None
)


def _span_type_for_name(name: str) -> str:
    """把内部 Span 名称映射为 TruLens 可识别的语义类型。"""

    return {
        "fitness.agent.request": "record_root",
        "fitness.agent.retrieval": "retrieval",
        "fitness.agent.generation": "generation",
        "fitness.agent.tool": "tool",
        "fitness.agent.reranker": "reranking",
    }.get(name, "agent")


def hash_identifier(value: str | None, *, secret: str = "") -> str | None:
    """返回用于追踪关联的稳定不可逆标识符。"""

    if not value:
        return None
    raw = value.encode("utf-8")
    if secret:
        return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:24]
    return hashlib.sha256(raw).hexdigest()[:24]


def redact_text(value: str | None, *, max_chars: int = 2000) -> str:
    """在评估采集前对常见个人信息和凭证形态的值进行脱敏。"""

    if not value:
        return ""
    redacted = _EMAIL.sub("<EMAIL>", value)
    redacted = _PHONE.sub("<PHONE>", redacted)
    redacted = _JWT.sub("<TOKEN>", redacted)
    redacted = _BEARER.sub("Bearer <TOKEN>", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1<SECRET>", redacted)
    redacted = _UUID.sub("<UUID>", redacted)
    redacted = _LONG_NUMBER.sub("<NUMBER>", redacted)
    redacted = " ".join(redacted.split())
    return redacted[:max_chars]


class TruLensTelemetry:
    """发出有界、低基数且兼容 TruLens 选择器的 span。"""

    def __init__(self, settings: Settings) -> None:
        self.enabled = bool(
            settings.trulens_enabled and settings.trulens_capture_mode != "disabled"
        )
        self.capture_content = settings.trulens_capture_mode == "evaluation"
        self.max_chars = settings.trulens_capture_max_chars
        self.tracer = trace.get_tracer(_TRACER_NAME, settings.service_version)
        self.app_id = settings.service_name
        self.app_name = settings.service_name
        self.app_version = settings.source_commit or settings.service_version
        self.code_version = settings.source_commit or settings.service_version
        self.prompt_version = settings.prompt_version
        self.knowledge_base_version = settings.knowledge_base_version
        self.graph_version = settings.graph_version
        self.identifier_hash_secret = settings.trulens_identifier_hash_secret

    @classmethod
    def disabled(cls) -> TruLensTelemetry:
        """创建用于单元测试和兼容性装配器的空操作实例。"""

        instance = object.__new__(cls)
        instance.enabled = False
        instance.capture_content = False
        instance.max_chars = 0
        instance.tracer = trace.get_tracer(_TRACER_NAME)
        instance.app_id = "fitness-agent-service"
        instance.app_name = "fitness-agent-service"
        instance.app_version = "disabled"
        instance.code_version = "disabled"
        instance.prompt_version = "disabled"
        instance.knowledge_base_version = "disabled"
        instance.graph_version = "disabled"
        instance.identifier_hash_secret = ""
        return instance

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any] | None = None,
        span_type: str | None = None,
    ) -> Iterator[Span]:
        """创建子 span，并仅附加允许列表中的标量元数据。"""

        if not self.enabled:
            yield trace.get_current_span()
            return
        with self.tracer.start_as_current_span(name) as current:
            self.set_attributes(
                current,
                {
                    TRULENS_APP_ID: self.app_id,
                    TRULENS_APP_NAME: self.app_name,
                    TRULENS_APP_VERSION: self.app_version,
                    TRULENS_SPAN_TYPE: span_type or _span_type_for_name(name),
                    TRULENS_RECORD_ID: _CURRENT_RECORD_ID.get(),
                    "fitness.agent.code_version": self.code_version,
                    "fitness.agent.prompt_version": self.prompt_version,
                    "fitness.agent.knowledge_base_version": self.knowledge_base_version,
                    "fitness.agent.graph_version": self.graph_version,
                },
            )
            self.set_attributes(current, attributes or {})
            yield current

    @contextmanager
    def request(
        self,
        *,
        request_id: str | None,
        trace_id: str | None,
        conversation_id: str | None,
        user_message: str | None,
        route: str | None = None,
    ) -> Iterator[Span]:
        """为一次 Supervisor 调用创建 TruLens 记录根 span。"""

        attributes: dict[str, Any] = {
            "fitness.agent.request_id_hash": hash_identifier(
                request_id, secret=self.identifier_hash_secret
            ),
            "fitness.agent.trace_id_hash": hash_identifier(
                trace_id, secret=self.identifier_hash_secret
            ),
            "fitness.agent.conversation_id_hash": hash_identifier(
                conversation_id, secret=self.identifier_hash_secret
            ),
            # TruLens 官方选择器使用 conversation_id 做会话级聚合；这里仍只写入
            # HMAC 摘要，既能关联同一会话，又不会把真实会话标识送入评测库。
            TRULENS_CONVERSATION_ID: hash_identifier(
                conversation_id, secret=self.identifier_hash_secret
            ),
            "fitness.agent.route": route,
        }
        if self.capture_content:
            attributes[RECORD_INPUT] = redact_text(user_message, max_chars=self.max_chars)
        record_id = hash_identifier(
            request_id or trace_id or conversation_id,
            secret=self.identifier_hash_secret,
        )
        token = _CURRENT_RECORD_ID.set(record_id)
        try:
            with self.span(
                "fitness.agent.request", attributes=attributes, span_type="record_root"
            ) as current:
                self.set_attributes(current, {TRULENS_RECORD_ID: record_id})
                yield current
        finally:
            _CURRENT_RECORD_ID.reset(token)

    def set_text(self, span: Span, key: str, value: str | None) -> None:
        """仅在显式评估采集模式下设置文本。"""

        if self.enabled and self.capture_content and value:
            span.set_attribute(key, redact_text(value, max_chars=self.max_chars))

    def set_attributes(self, span: Span, attributes: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        for key, value in attributes.items():
            if value is None:
                continue
            if isinstance(value, (str, bool, int, float)):
                span.set_attribute(key, value)
            elif isinstance(value, (list, tuple)) and all(
                isinstance(item, (str, bool, int, float)) for item in value
            ):
                span.set_attribute(key, list(value))

    def finish_request(self, span: Span, *, answer: str | None, status: str) -> None:
        if not self.enabled:
            return
        span.set_attribute("fitness.agent.status", status)
        span.set_attribute("fitness.agent.outcome", status)
        if answer:
            self.set_text(span, RECORD_OUTPUT, answer)

    def finish_retrieval(
        self,
        span: Span,
        *,
        query: str,
        contexts: list[str],
        knowledge_versions: list[str] | None = None,
    ) -> None:
        if not self.enabled:
            return
        span.set_attribute(RETRIEVAL_COUNT, len(contexts))
        if self.capture_content:
            captured_contexts = [
                redact_text(item, max_chars=self.max_chars) for item in contexts[:8]
            ]
            span.set_attribute(RETRIEVAL_QUERY, redact_text(query, max_chars=self.max_chars))
            span.set_attribute(
                RETRIEVAL_CONTEXTS,
                captured_contexts,
            )
            span.set_attribute("fitness.agent.contexts", captured_contexts)
        if knowledge_versions:
            span.set_attribute(
                "fitness.agent.knowledge_versions",
                sorted({str(version) for version in knowledge_versions})[:16],
            )
