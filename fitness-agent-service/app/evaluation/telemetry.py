"""注重隐私的 TruLens/OpenTelemetry 集成。

应用已经拥有进程级 OpenTelemetry provider。本模块只创建子 span 并发出 TruLens 语义属性；
不会安装第二个 provider，也不会将凭证、签名上下文、确认 Payload 或原始工具参数放入 span。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

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

# TruLens 语义约定。保留字符串回退值，使精简 API 镜像未安装可选评估额外依赖时，
# 核心 API 仍可运行；启用额外依赖后，发出的属性仍可被正常消费。
RECORD_INPUT = "ai.observability.record_root.input"
RECORD_OUTPUT = "ai.observability.record_root.output"
RETRIEVAL_QUERY = "ai.observability.retrieval.query_text"
RETRIEVAL_CONTEXTS = "ai.observability.retrieval.retrieved_contexts"
RETRIEVAL_COUNT = "ai.observability.retrieval.num_contexts"


def hash_identifier(value: str | None) -> str | None:
    """返回用于追踪关联的稳定不可逆标识符。"""

    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def redact_text(value: str | None, *, max_chars: int = 2000) -> str:
    """在评估采集前对常见个人信息和凭证形态的值进行脱敏。"""

    if not value:
        return ""
    redacted = _EMAIL.sub("<EMAIL>", value)
    redacted = _PHONE.sub("<PHONE>", redacted)
    redacted = _JWT.sub("<TOKEN>", redacted)
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

    @classmethod
    def disabled(cls) -> TruLensTelemetry:
        """创建用于单元测试和兼容性装配器的空操作实例。"""

        instance = object.__new__(cls)
        instance.enabled = False
        instance.capture_content = False
        instance.max_chars = 0
        instance.tracer = trace.get_tracer(_TRACER_NAME)
        return instance

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        """创建子 span，并仅附加允许列表中的标量元数据。"""

        if not self.enabled:
            yield trace.get_current_span()
            return
        with self.tracer.start_as_current_span(name) as current:
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
            "fitness.agent.request_id_hash": hash_identifier(request_id),
            "fitness.agent.trace_id_hash": hash_identifier(trace_id),
            "fitness.agent.conversation_id_hash": hash_identifier(conversation_id),
            "fitness.agent.route": route,
        }
        if self.capture_content:
            attributes[RECORD_INPUT] = redact_text(user_message, max_chars=self.max_chars)
        with self.span("fitness.agent.request", attributes=attributes) as current:
            yield current

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
        if answer:
            self.set_text(span, RECORD_OUTPUT, answer)

    def finish_retrieval(self, span: Span, *, query: str, contexts: list[str]) -> None:
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
