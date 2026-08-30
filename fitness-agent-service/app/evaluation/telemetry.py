"""Privacy-aware TruLens/OpenTelemetry integration.

The application already owns the process-wide OpenTelemetry provider.  This module
only creates child spans and emits TruLens semantic attributes; it never installs a
second provider and never puts credentials, signed contexts, confirmation payloads,
or raw tool arguments into a span.
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

# TruLens semantic conventions.  Keeping string fallbacks here means the core API
# can still run when the optional evaluation extra is not installed in a lean API
# image; the emitted attributes remain consumable after the extra is enabled.
RECORD_INPUT = "ai.observability.record_root.input"
RECORD_OUTPUT = "ai.observability.record_root.output"
RETRIEVAL_QUERY = "ai.observability.retrieval.query_text"
RETRIEVAL_CONTEXTS = "ai.observability.retrieval.retrieved_contexts"
RETRIEVAL_COUNT = "ai.observability.retrieval.num_contexts"


def hash_identifier(value: str | None) -> str | None:
    """Return a stable non-reversible identifier for trace correlation."""

    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def redact_text(value: str | None, *, max_chars: int = 2000) -> str:
    """Redact common PII and credential-shaped values before evaluation capture."""

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
    """Emit bounded, low-cardinality spans compatible with TruLens selectors."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = bool(
            settings.trulens_enabled and settings.trulens_capture_mode != "disabled"
        )
        self.capture_content = settings.trulens_capture_mode == "evaluation"
        self.max_chars = settings.trulens_capture_max_chars
        self.tracer = trace.get_tracer(_TRACER_NAME, settings.service_version)

    @classmethod
    def disabled(cls) -> TruLensTelemetry:
        """Create a no-op instance for unit tests and compatibility assemblers."""

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
        """Create a child span and attach only allow-listed scalar metadata."""

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
        """Create a TruLens record-root span for one Supervisor invocation."""

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
        """Set text only in explicit evaluation-capture mode."""

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
