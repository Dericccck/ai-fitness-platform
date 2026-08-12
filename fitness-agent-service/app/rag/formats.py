"""将多格式文档解析为保留结构的 RAG 内容块。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
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
    """提取 PDF 页面文本和表格，并在进入分块前完成一轮保守清洗。

    PDF 的文本层经常把网页导航、页眉页脚和字体映射字符混入正文。这里先按页收集
    原始行，再做模板过滤、重复行识别、Unicode 规范化和有限的段落重组，最后才生成
    ``ParsedBlock``。这样父节点不会直接继承整页的版式噪声；原始文件仍由对象存储或
    本地资料目录保留，清洗规则变化后可以重新解析。
    """

    def __init__(self, *, ocr_provider: PdfOcrProvider | None = None) -> None:
        self.ocr_provider = ocr_provider

    def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
        import pdfplumber

        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        missing_pages: list[int] = []
        with pdfplumber.open(BytesIO(content)) as document:
            page_records: list[tuple[int, list[str], list[list[list[str | None]]]]] = []
            for page_number, page in enumerate(document.pages, start=1):
                tables = _extract_pdf_tables(page)
                raw_text = _extract_pdf_text_outside_tables(page, tables)
                raw_lines = [line for line in raw_text.splitlines() if line.strip()]
                page_records.append((page_number, raw_lines, tables))

            repeated_lines = _repeated_pdf_lines([raw_lines for _, raw_lines, _ in page_records])
            for page_number, raw_lines, tables in page_records:
                cleaned_lines = _clean_pdf_lines(raw_lines, repeated_lines=repeated_lines)
                if _is_pdf_toc_page(cleaned_lines):
                    cleaned_lines = []
                for text_block in _split_pdf_text_blocks(cleaned_lines):
                    blocks.append(
                        ParsedBlock(
                            kind="TEXT",
                            content=text_block,
                            source_page=page_number,
                            metadata={"parser": "pdfplumber", "cleaned": True},
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
                                row_start=1,
                                row_end=len(table),
                                metadata={
                                    "parser": "pdfplumber",
                                    "cleaned": True,
                                    "table_header_repeated": True,
                                },
                            )
                        )
                if not raw_lines and not tables:
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


_PDF_TOC_MARKERS = ("table of contents", "目录")
_PDF_DOT_LEADER = re.compile(r"(?:\.{4,}|·{4,}|…{2,})\s*\d+\s*$")
_PDF_TEMPLATE_PATTERNS = (
    re.compile(r"无障碍浏览\s*网站导航\s*公务员邮箱"),
    re.compile(r"请输入关键字\s*搜"),
    re.compile(r"^(?:首\s*页|首页)\s*[>＞].*$"),
    re.compile(r"^首\s*页\s+机\s*构\s+公\s*开\s+资\s*讯\s+服\s*务\s+互\s*动$"),
    re.compile(r"(?:打印此页|关闭窗口|关闭窗⼜|print this page|close window)", re.IGNORECASE),
    re.compile(r"(?:版权所有|网站标识码|ICP备|公安网安备|通讯地址|邮政编码|信访电话)"),
    re.compile(r"^联系电话[:：]|^字体[:：]\s*[大中小]+$|^发布时间[:：]"),
    re.compile(r"^(?:accessibility|site navigation|copyright)\b", re.IGNORECASE),
)
_PDF_SENTENCE_END = frozenset("。！？.!?；;：:")
_CJK = re.compile(r"[\u3400-\u9fff]")
_PDF_ENGLISH_HEADING_WORDS = frozenset(
    ["and", "or", "the", "of", "for", "to", "in", "on", "with", "from", "among"]
)


def _normalize_pdf_line(line: str) -> str:
    """修复 PDF 常见的兼容字符和空白，但不改动正文数值。"""

    normalized = unicodedata.normalize("NFKC", line).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _is_pdf_template_line(line: str) -> bool:
    """判断网页打印模板行，规则只删除高置信度的导航/版权内容。"""

    return any(pattern.search(line) for pattern in _PDF_TEMPLATE_PATTERNS)


def _is_pdf_layout_noise_table(table: Sequence[Sequence[str | None]]) -> bool:
    """过滤网页导航布局表格，但保留正文业务表格。"""

    cells = [
        _normalize_pdf_line(str(cell))
        for row in table
        for cell in row
        if cell is not None and str(cell).strip()
    ]
    if not cells:
        return True
    joined = " ".join(cells)
    return any(
        marker in joined
        for marker in (
            "无障碍浏览",
            "网站导航",
            "公务员邮箱",
            "请输入关键字",
            "首页 >",
            "打印此页",
            "关闭窗口",
            "版权所有",
        )
    )


def _repeated_pdf_lines(page_lines: Sequence[Sequence[str]]) -> frozenset[str]:
    """识别出现在页首/页尾且跨页重复的页眉页脚，避免误删正文标题。"""

    counts: Counter[str] = Counter()
    edge_counts: Counter[str] = Counter()
    for lines in page_lines:
        normalized_lines = [_normalize_pdf_line(line) for line in lines if line.strip()]
        counts.update(normalized_lines)
        edge_lines = normalized_lines[:3] + normalized_lines[-3:]
        edge_counts.update(edge_lines)
    return frozenset(
        line
        for line, count in counts.items()
        if count >= 2
        and edge_counts[line] >= 2
        and len(line) <= 180
        and line[-1:] not in _PDF_SENTENCE_END
    )


def _clean_pdf_lines(
    raw_lines: Sequence[str],
    *,
    repeated_lines: frozenset[str] = frozenset(),
) -> list[str]:
    """清除高置信度模板行、目录页码点线，并统一 PDF 字体映射字符。"""

    cleaned: list[str] = []
    for raw_line in raw_lines:
        line = _normalize_pdf_line(raw_line)
        if not line or _is_pdf_template_line(line):
            continue
        if line in repeated_lines or _PDF_DOT_LEADER.search(line):
            continue
        cleaned.append(line)
    return cleaned


def _is_pdf_toc_page(lines: Sequence[str]) -> bool:
    """检测目录页并整页排除，避免只删除页码后留下无语义的标题列表。"""

    lowered_lines = [line.lower().strip() for line in lines[:12]]
    has_toc_marker = any(
        line == marker or line == "contents" or line.startswith(f"{marker} ")
        for line in lowered_lines
        for marker in _PDF_TOC_MARKERS
    )
    return has_toc_marker and len(lines) >= 3


def _is_pdf_structural_line(line: str) -> bool:
    """识别标题、列表和编号项，用于限制父节点范围。"""

    if line.startswith(("•", "▪", "- ", "* ")):
        return True
    if re.match(r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d+[、.)])", line):
        return True
    if line.endswith(("？", "!", "?", "!")):
        return True
    if re.match(r"^(?:第\s*\S+章|Chapter\s+\d+|Appendix\s+\d+)", line, re.IGNORECASE):
        return True
    if _CJK.search(line):
        # 中文正文在 PDF 中常被按视觉宽度拆成多行，不能仅凭“短”就切开；
        # 只有很短且没有逗号/冒号等连接标点的行，才保守地视为小标题。
        return (
            len(line) <= 16
            and not any(mark in line for mark in "，,；;：:、")
            and not line.endswith(tuple(_PDF_SENTENCE_END))
        )
    # 英文 PDF 也会把一个段落按版心宽度拆成多行，因此不能把所有短行都当标题。
    # 仅接受标题式大小写、明确的章节名或版本名，降低普通正文被切碎的概率。
    if re.match(r"^(?:\d+(?:st|nd|rd|th)?\s+edition)$", line, re.IGNORECASE):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", line)
    if not words or len(line) > 64 or len(words) > 8:
        return False
    return all(word[0].isupper() or word.lower() in _PDF_ENGLISH_HEADING_WORDS for word in words)


def _join_pdf_lines(lines: Sequence[str]) -> str:
    """按中英文边界合并 PDF 换行，避免中文被插入空格、英文单词被粘连。"""

    result = ""
    for line in lines:
        if not result:
            result = line
            continue
        previous = result[-1:]
        first = line[:1]
        if previous == "-" and first.isascii() and first.isalpha():
            result = result[:-1] + line
        elif (
            (_CJK.search(previous) and _CJK.search(first))
            or (previous in "。！？；：，," and _CJK.search(first))
            or previous in "([{／/"
            or first in "，。；：！？、)]}】》%％,.!?;:/"
        ):
            result += line
        else:
            result += " " + line
    return result.strip()


def _split_pdf_text_blocks(lines: Sequence[str], *, max_block_chars: int = 900) -> list[str]:
    """将页面文本切成有界上下文块，避免父节点直接膨胀为整页。"""

    blocks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            content = _join_pdf_lines(current)
            if content:
                blocks.append(content)
        current = []
        current_length = 0

    for line in lines:
        if current and (_is_pdf_structural_line(current[-1]) or _is_pdf_structural_line(line)):
            flush()
        current.append(line)
        current_length += len(line)
        if (current_length >= max_block_chars and line[-1:] in _PDF_SENTENCE_END) or (
            current_length >= max_block_chars + 180
        ):
            flush()
    flush()
    return blocks


def _extract_pdf_tables(page: object) -> list[list[list[str | None]]]:
    """优先使用带坐标的表格对象，为正文排除表格区域并保留表头。"""

    try:
        tables = page.find_tables()  # type: ignore[attr-defined]
        extracted = [table.extract() for table in tables]
    except Exception:  # noqa: BLE001 - 单页表格失败时回退到 pdfplumber 基础接口
        extracted = []
    extracted = [table for table in extracted if not _is_pdf_layout_noise_table(table)]
    if extracted:
        return extracted
    try:
        fallback = page.extract_tables() or []  # type: ignore[attr-defined]
        return [table for table in fallback if not _is_pdf_layout_noise_table(table)]
    except Exception:  # noqa: BLE001 - 解析失败由空表交给正文/OCR流程处理
        return []


def _extract_pdf_text_outside_tables(
    page: object,
    tables: Sequence[Sequence[Sequence[str | None]]],
) -> str:
    """提取页面正文；表格坐标可用时先从文本层排除表格，避免表格重复入库。"""

    # ``extract_tables`` 的回退结果没有坐标，不能猜测区域；此时保留文本并依赖后续
    # 质量评测发现重复，避免错误裁剪正文。
    try:
        table_objects = page.find_tables()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        table_objects = []
    if not table_objects or not tables:
        return page.extract_text(layout=False) or ""  # type: ignore[attr-defined]
    boxes = [table.bbox for table in table_objects]

    def keep_object(obj: dict[str, object]) -> bool:
        x0, x1 = obj.get("x0"), obj.get("x1")
        top, bottom = obj.get("top"), obj.get("bottom")
        if not all(isinstance(value, (int, float)) for value in (x0, x1, top, bottom)):
            return True
        assert isinstance(x0, (int, float))
        assert isinstance(x1, (int, float))
        assert isinstance(top, (int, float))
        assert isinstance(bottom, (int, float))
        center_x = (x0 + x1) / 2
        center_y = (top + bottom) / 2
        return not any(
            left <= center_x <= right and upper <= center_y <= lower
            for left, upper, right, lower in boxes
        )

    try:
        filtered_page = page.filter(keep_object)  # type: ignore[attr-defined]
        return filtered_page.extract_text(layout=False) or ""
    except Exception:  # noqa: BLE001 - 坐标过滤失败时保留原文本，不丢正文
        return page.extract_text(layout=False) or ""  # type: ignore[attr-defined]


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
