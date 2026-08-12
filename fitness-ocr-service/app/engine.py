"""PaddleOCR adapter and canonical result conversion."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol

from .config import Settings
from .models import OcrBlock


class OcrEngineUnavailable(RuntimeError):
    """The inference runtime or model cannot be loaded."""


class OcrEngineError(RuntimeError):
    """The inference engine returned an unusable result."""


class DocumentEngine(Protocol):
    """Minimal engine boundary used by the service and its tests."""

    def status(self) -> EngineStatus:
        """Return whether the underlying model runtime is ready."""

    def predict(self, input_path: str) -> Iterable[Any]:
        """Yield one Paddle-style result for each processed page."""


@dataclass(frozen=True)
class EngineStatus:
    """Readiness state exposed without leaking model internals."""

    ready: bool
    engine_name: str
    error: str | None = None


class PaddleStructureEngine:
    """Run PaddleOCR PP-StructureV3 and expose only sanitized page results.

    The import and model construction happen once, on first readiness check. This
    keeps contract tests usable on machines without Paddle while production readiness
    still fails until the real model is available.
    """

    name = "paddleocr-pp-structurev3"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pipeline: Any | None = None
        self._load_error: str | None = None

    def status(self) -> EngineStatus:
        """Load the model once and return a safe readiness summary."""

        if self._pipeline is not None:
            return EngineStatus(True, self.name)
        if self._load_error is not None:
            return EngineStatus(False, self.name, self._load_error)
        try:
            from paddleocr import PPStructureV3  # type: ignore[import-not-found]

            # Keep model construction centralized so a future PaddleOCR-VL or cloud
            # adapter can replace this class without changing the HTTP contract.
            self._pipeline = PPStructureV3(
                lang=self.settings.language,
                device=self.settings.device,
                use_doc_orientation_classify=self.settings.use_doc_orientation_classify,
                use_doc_unwarping=self.settings.use_doc_unwarping,
                use_textline_orientation=self.settings.use_textline_orientation,
                use_table_recognition=self.settings.use_table_recognition,
                use_formula_recognition=self.settings.use_formula_recognition,
                format_block_content=self.settings.format_block_content,
            )
        except Exception as exc:  # noqa: BLE001 - model runtimes expose heterogeneous errors.
            self._load_error = f"PaddleOCR engine is unavailable: {exc}"
            return EngineStatus(False, self.name, self._load_error)
        return EngineStatus(True, self.name)

    def predict(self, input_path: str) -> Iterator[Any]:
        """Yield raw Paddle results after enforcing model readiness."""

        status = self.status()
        if not status.ready or self._pipeline is None:
            raise OcrEngineUnavailable(status.error or "OCR engine is unavailable")
        try:
            yield from self._pipeline.predict(input_path)
        except Exception as exc:
            raise OcrEngineError(f"PaddleOCR inference failed: {exc}") from exc


def result_to_mapping(result: Any) -> Mapping[str, Any]:
    """Convert a Paddle result object into a JSON-like mapping."""

    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, Mapping):
        return payload
    raise OcrEngineError("OCR engine returned a non-object result")


def blocks_from_page_result(
    result: Any,
    *,
    source_page: int,
    table_index_start: int,
) -> tuple[list[OcrBlock], int]:
    """Convert one Paddle page into contract blocks with source coordinates."""

    payload = result_to_mapping(result)
    raw_blocks = payload.get("parsing_res_list", [])
    if not isinstance(raw_blocks, list):
        raise OcrEngineError("OCR page result parsing_res_list must be an array")

    blocks: list[OcrBlock] = []
    heading_path: list[str] = []
    table_index = table_index_start
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping):
            raise OcrEngineError("OCR page block must be an object")
        label = _safe_text(raw_block.get("block_label"), "text").lower()
        content = _safe_text(raw_block.get("block_content"), "").strip()
        if not content:
            continue

        is_table = label in {"table", "table_caption"} or "<table" in content.lower()
        if is_table:
            normalized = html_table_to_markdown(content)
            if not normalized:
                continue
            blocks.append(
                OcrBlock(
                    kind="TABLE",
                    content=normalized,
                    heading_path=list(heading_path),
                    source_page=source_page,
                    table_index=table_index,
                    metadata={"ocr_engine": "paddleocr-pp-structurev3", "block_label": label},
                )
            )
            table_index += 1
            continue

        if label in {"title", "doc_title", "section_header", "header"}:
            heading_path = [content]
            continue
        blocks.append(
            OcrBlock(
                kind="TEXT",
                content=content,
                heading_path=list(heading_path),
                source_page=source_page,
                metadata={"ocr_engine": "paddleocr-pp-structurev3", "block_label": label},
            )
        )
    return blocks, table_index


def _safe_text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


class _TableParser(HTMLParser):
    """Small dependency-free HTML table reader for Paddle table blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_row is not None:
            self._current_row.append(" ".join(self._current_cell or []).strip())
            self._current_cell = None
        elif lowered == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(html.unescape(data))


def html_table_to_markdown(content: str) -> str:
    """Keep Markdown unchanged, otherwise convert Paddle HTML table output."""

    if "<table" not in content.lower():
        return re.sub(r"\s+", " ", content).strip()
    parser = _TableParser()
    parser.feed(content)
    rows = [row for row in parser.rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)
