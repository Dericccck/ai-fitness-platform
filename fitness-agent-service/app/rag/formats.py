"""Multi-format document parsing into structure-preserving RAG blocks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal, Protocol

from .text import clean_markdown

BlockKind = Literal["TEXT", "TABLE"]


class UnsupportedDocumentFormatError(ValueError):
    """The ingestion registry has no safe parser for the source format."""


class DocumentParseError(ValueError):
    """A supported file could not be parsed or contained no usable content."""


@dataclass(frozen=True)
class ParsedBlock:
    """Canonical text/table block with source coordinates preserved."""

    kind: BlockKind
    content: str
    heading_path: tuple[str, ...] = ()
    source_page: int | None = None
    source_sheet: str | None = None
    table_index: int | None = None
    row_start: int | None = None
    row_end: int | None = None
    metadata: dict[str, str | int | bool] | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """Parser output before chunking and Embedding."""

    blocks: tuple[ParsedBlock, ...]
    media_type: str
    warnings: tuple[str, ...] = ()


class DocumentParser(Protocol):
    """Parser contract implemented by each supported file format."""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        """Parse bytes without writing untrusted uploads to a local path."""


class PdfOcrProvider(Protocol):
    """OCR boundary for scanned PDFs; implementation belongs to a dedicated service."""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        """Return structure-preserving OCR blocks for one PDF."""


class DocumentParserRegistry:
    """Select a parser by a normalized extension and enforce upload size limits."""

    def __init__(
        self,
        *,
        max_source_bytes: int = 20 * 1024 * 1024,
        pdf_ocr_provider: PdfOcrProvider | None = None,
    ) -> None:
        self.max_source_bytes = max_source_bytes
        self._parsers: dict[str, DocumentParser] = {
            ".md": MarkdownParser(),
            ".markdown": MarkdownParser(),
            ".txt": MarkdownParser(),
            ".pdf": PdfParser(ocr_provider=pdf_ocr_provider),
            ".docx": DocxParser(),
            ".xlsx": XlsxParser(),
        }

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        """Parse a supported extension and reject oversized/empty uploads early."""

        if not content:
            raise DocumentParseError("document content must not be empty")
        if len(content) > self.max_source_bytes:
            raise DocumentParseError("document exceeds the configured size limit")
        suffix = PurePosixPath(file_name).suffix.lower()
        parser = self._parsers.get(suffix)
        if parser is None:
            supported = ", ".join(sorted(self._parsers))
            raise UnsupportedDocumentFormatError(
                f"unsupported document extension {suffix or '<none>'}; supported: {supported}"
            )
        try:
            parsed = parser.parse(content, file_name=file_name)
        except (DocumentParseError, UnsupportedDocumentFormatError):
            raise
        except Exception as exc:
            raise DocumentParseError(f"failed to parse {file_name}") from exc
        if not parsed.blocks:
            raise DocumentParseError(f"document {file_name} contains no indexable content")
        return parsed


class MarkdownParser:
    """Parse Markdown/text while keeping the existing heading-aware chunker path."""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        try:
            raw = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentParseError("Markdown/text must be UTF-8") from exc
        cleaned = clean_markdown(raw)
        return ParsedDocument(
            blocks=(ParsedBlock(kind="TEXT", content=cleaned),),
            media_type="text/markdown"
            if file_name.lower().endswith((".md", ".markdown"))
            else "text/plain",
        )


class PdfParser:
    """Extract PDF page text and layout-aware tables with source page metadata."""

    def __init__(self, *, ocr_provider: PdfOcrProvider | None = None) -> None:
        self.ocr_provider = ocr_provider

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        import pdfplumber

        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        with pdfplumber.open(BytesIO(content)) as document:
            for page_number, page in enumerate(document.pages, start=1):
                text = (page.extract_text(layout=True) or "").strip()
                tables = page.extract_tables() or []
                if text:
                    blocks.append(
                        ParsedBlock(
                            kind="TEXT",
                            content=text,
                            source_page=page_number,
                            metadata={"parser": "pdfplumber"},
                        )
                    )
                for table_index, table in enumerate(tables):
                    normalized = _table_to_markdown(table)
                    if normalized:
                        blocks.append(
                            ParsedBlock(
                                kind="TABLE",
                                content=normalized,
                                source_page=page_number,
                                table_index=table_index,
                                metadata={"parser": "pdfplumber"},
                            )
                        )
                if not text and not tables:
                    warnings.append(f"page {page_number} has no extractable text or table")
        if not blocks:
            if self.ocr_provider is not None:
                return self.ocr_provider.parse(content, file_name=file_name)
            raise DocumentParseError(
                "PDF contains no extractable text; scanned PDFs require an approved OCR pipeline"
            )
        return ParsedDocument(tuple(blocks), "application/pdf", tuple(warnings))


class DocxParser:
    """Read DOCX paragraphs and tables in document order with heading context."""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        from docx import Document
        from docx.document import Document as DocumentType
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document: DocumentType = Document(BytesIO(content))
        blocks: list[ParsedBlock] = []
        heading_path: list[str] = []
        table_index = 0
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph = Paragraph(child, document)
                text = " ".join(paragraph.text.split()).strip()
                if not text:
                    continue
                style_name = paragraph.style.name if paragraph.style else ""
                if style_name.lower().startswith("heading"):
                    level = _heading_level(style_name)
                    heading_path[:] = heading_path[: level - 1]
                    heading_path.append(text)
                    continue
                blocks.append(
                    ParsedBlock(kind="TEXT", content=text, heading_path=tuple(heading_path))
                )
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
                normalized = _table_to_markdown(rows)
                if normalized:
                    blocks.append(
                        ParsedBlock(
                            kind="TABLE",
                            content=normalized,
                            heading_path=tuple(heading_path),
                            table_index=table_index,
                        )
                    )
                    table_index += 1
        if not blocks:
            raise DocumentParseError("DOCX contains no indexable paragraphs or tables")
        return ParsedDocument(
            tuple(blocks), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


class XlsxParser:
    """Convert non-empty worksheets into header-preserving table blocks."""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        from openpyxl import load_workbook  # type: ignore[import-untyped]

        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        try:
            for sheet in workbook.worksheets:
                rows = [
                    ["" if value is None else str(value).strip() for value in row]
                    for row in sheet.iter_rows(values_only=True)
                ]
                rows = [row for row in rows if any(cell for cell in row)]
                if not rows:
                    warnings.append(f"worksheet {sheet.title} is empty")
                    continue
                normalized = _table_to_markdown(rows)
                if normalized:
                    blocks.append(
                        ParsedBlock(
                            kind="TABLE",
                            content=normalized,
                            heading_path=(sheet.title,),
                            source_sheet=sheet.title,
                            table_index=len(blocks),
                            metadata={"parser": "openpyxl", "worksheet": sheet.title},
                        )
                    )
        finally:
            workbook.close()
        if not blocks:
            raise DocumentParseError("XLSX contains no non-empty worksheets")
        return ParsedDocument(
            tuple(blocks),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            tuple(warnings),
        )


def _heading_level(style_name: str) -> int:
    """Read a DOCX Heading 1..6 style, clamping unknown heading styles safely."""

    match = next((char for char in style_name if char.isdigit()), "1")
    return max(1, min(6, int(match)))


def _table_to_markdown(rows: Sequence[Sequence[str | None]]) -> str:
    """Render tables with a repeated header so row chunks remain self-describing."""

    cleaned_rows = [
        ["" if cell is None else _escape_cell(str(cell)) for cell in row] for row in rows
    ]
    cleaned_rows = [row for row in cleaned_rows if any(cell.strip() for cell in row)]
    if not cleaned_rows:
        return ""
    width = max(len(row) for row in cleaned_rows)
    padded = [row + [""] * (width - len(row)) for row in cleaned_rows]
    header = padded[0]
    separator = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    """Keep pipe-delimited table structure intact when a cell contains ``|``."""

    return " ".join(value.replace("|", "\\|").split())
