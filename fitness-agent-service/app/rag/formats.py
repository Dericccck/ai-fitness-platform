"""将多格式文档解析为保留结构的 RAG 内容块。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal, Protocol

from .text import clean_markdown

BlockKind = Literal["TEXT", "TABLE"]


class UnsupportedDocumentFormatError(ValueError):
    """入库解析器注册表没有适用于该来源格式的安全解析器。"""


class DocumentParseError(ValueError):
    """支持的文件无法解析，或不包含可用内容。"""


@dataclass(frozen=True)
class ParsedBlock:
    """保留来源坐标的标准文本/表格内容块。"""

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
    """分块和 Embedding 前的解析器输出。"""

    blocks: tuple[ParsedBlock, ...]
    media_type: str
    warnings: tuple[str, ...] = ()


class DocumentParser(Protocol):
    """每种支持的文件格式都需要实现的解析器契约。"""

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        """解析字节内容，不将不可信上传文件写入本地路径。"""


class PdfOcrProvider(Protocol):
    """扫描型 PDF 的 OCR 边界，具体实现属于独立 OCR 服务。"""

    def parse(
        self,
        content: bytes,
        *,
        file_name: str,
        pages: Sequence[int] = (),
    ) -> ParsedDocument:
        """返回指定 PDF 页面中保留结构的 OCR 内容块。"""


class DocumentParserRegistry:
    """根据标准化扩展名选择解析器，并执行上传大小限制。"""

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
        """解析支持的扩展名，并尽早拒绝超大或空上传文件。"""

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
    """解析 Markdown/文本，同时保留现有的标题感知分块路径。"""

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
    """提取 PDF 页面文本和布局感知表格，并保留来源页码元数据。"""

    def __init__(self, *, ocr_provider: PdfOcrProvider | None = None) -> None:
        self.ocr_provider = ocr_provider

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        import pdfplumber

        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        missing_pages: list[int] = []
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
                    missing_pages.append(page_number)
                    warnings.append(f"page {page_number} has no extractable text or table")
        if missing_pages and self.ocr_provider is not None:
            ocr_document = self.ocr_provider.parse(
                content,
                file_name=file_name,
                pages=tuple(missing_pages),
            )
            blocks.extend(ocr_document.blocks)
            warnings.extend(ocr_document.warnings)
        if not blocks:
            if self.ocr_provider is not None:
                return self.ocr_provider.parse(content, file_name=file_name)
            raise DocumentParseError(
                "PDF contains no extractable text; scanned PDFs require an approved OCR pipeline"
            )
        return ParsedDocument(tuple(blocks), "application/pdf", tuple(warnings))


class DocxParser:
    """按 DOCX 文档顺序读取段落和表格，并保留标题上下文。"""

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
    """将非空工作表转换为保留表头的表格内容块。"""

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
    """读取 DOCX Heading 1..6 样式，对未知标题样式进行安全限制。"""

    match = next((char for char in style_name if char.isdigit()), "1")
    return max(1, min(6, int(match)))


def _table_to_markdown(rows: Sequence[Sequence[str | None]]) -> str:
    """渲染表格并重复表头，使按行切分的内容块仍能自描述。"""

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
    """单元格包含 ``|`` 时，仍保持管道分隔表格结构完整。"""

    return " ".join(value.replace("|", "\\|").split())
