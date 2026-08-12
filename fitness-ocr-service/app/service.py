"""OCR HTTP 服务的 PDF 请求编排。"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .config import Settings
from .engine import DocumentEngine, OcrEngineError, blocks_from_page_result
from .models import OcrBlock, OcrResponse


class OcrInputError(ValueError):
    """调用方提交了无效或不支持的 PDF 请求。"""


class PdfOcrService:
    """校验一个 PDF OCR 请求，可选裁剪页面并结构化结果。"""

    def __init__(self, settings: Settings, engine: DocumentEngine) -> None:
        self.settings = settings
        self.engine = engine

    def parse(self, content: bytes, *, pages: str | None) -> OcrResponse:
        """解析指定页面，同时保留原始的从 1 开始的页码。"""

        if not content:
            raise OcrInputError("file must not be empty")
        if not content.startswith(b"%PDF-"):
            raise OcrInputError("file must be a PDF")
        try:
            reader = PdfReader(BytesIO(content))
            total_pages = len(reader.pages)
        except Exception as exc:
            raise OcrInputError("PDF is malformed or unreadable") from exc
        if total_pages == 0:
            raise OcrInputError("PDF contains no pages")
        if total_pages > self.settings.max_pages:
            raise OcrInputError(f"PDF exceeds the {self.settings.max_pages}-page limit")

        selected_pages = _parse_pages(pages, total_pages)
        with _temporary_pdf(reader, selected_pages) as input_path:
            try:
                raw_results = self.engine.predict(str(input_path))
                blocks: list[OcrBlock] = []
                warnings: list[str] = []
                table_index = 0
                for result_index, result in enumerate(raw_results):
                    source_page = (
                        selected_pages[result_index]
                        if result_index < len(selected_pages)
                        else result_index + 1
                    )
                    page_blocks, table_index = blocks_from_page_result(
                        result,
                        source_page=source_page,
                        table_index_start=table_index,
                    )
                    if not page_blocks:
                        warnings.append(f"page {source_page} produced no indexable OCR blocks")
                    blocks.extend(page_blocks)
            except OcrInputError:
                raise
            except Exception as exc:
                if isinstance(exc, OcrEngineError):
                    raise
                raise OcrEngineError("OCR result conversion failed") from exc
        if not blocks:
            raise OcrEngineError("OCR produced no indexable blocks")
        return OcrResponse(warnings=warnings, blocks=blocks)


def _parse_pages(value: str | None, total_pages: int) -> list[int]:
    if not value or not value.strip():
        return list(range(1, total_pages + 1))
    pages: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token.isdigit() or int(token) < 1:
            raise OcrInputError("pages must be a comma-separated list of positive integers")
        page = int(token)
        if page > total_pages:
            raise OcrInputError(f"requested page {page} is outside the PDF")
        if page not in pages:
            pages.append(page)
    if not pages:
        raise OcrInputError("pages must select at least one page")
    return pages


class _temporary_pdf:
    """创建临时 PDF 页面子集并在使用后删除的上下文管理器。"""

    def __init__(self, reader: PdfReader, pages: Sequence[int]) -> None:
        self.reader = reader
        self.pages = pages
        self.path: Path | None = None

    def __enter__(self) -> Path:
        writer = PdfWriter()
        for page_number in self.pages:
            writer.add_page(self.reader.pages[page_number - 1])
        handle = tempfile.NamedTemporaryFile(prefix="fitness-ocr-", suffix=".pdf", delete=False)
        self.path = Path(handle.name)
        try:
            with handle:
                writer.write(handle)
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)
