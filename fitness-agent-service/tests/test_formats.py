from io import BytesIO

import pytest

from app.rag.formats import (
    DocumentParseError,
    DocumentParserRegistry,
    UnsupportedDocumentFormatError,
)


def test_registry_parses_markdown_and_rejects_unknown_extensions() -> None:
    registry = DocumentParserRegistry(max_source_bytes=1000)

    parsed = registry.parse(b"# Warmup\n\nPrepare the hips.", file_name="guide.md")

    assert parsed.media_type == "text/markdown"
    assert parsed.blocks[0].content == "# Warmup\n\nPrepare the hips."
    with pytest.raises(UnsupportedDocumentFormatError):
        registry.parse(b"content", file_name="guide.rtf")


def test_registry_enforces_source_size_before_parser_work() -> None:
    registry = DocumentParserRegistry(max_source_bytes=3)

    with pytest.raises(DocumentParseError, match="size limit"):
        registry.parse(b"abcd", file_name="guide.txt")


def test_docx_parser_preserves_heading_and_table_structure() -> None:
    from docx import Document

    document = Document()
    document.add_heading("Strength Training", level=1)
    document.add_paragraph("Use a controlled range of motion.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Exercise"
    table.rows[0].cells[1].text = "Sets"
    row = table.add_row()
    row.cells[0].text = "Squat"
    row.cells[1].text = "4"
    payload = BytesIO()
    document.save(payload)

    parsed = DocumentParserRegistry().parse(payload.getvalue(), file_name="guide.docx")

    assert parsed.blocks[0].heading_path == ("Strength Training",)
    assert parsed.blocks[0].content == "Use a controlled range of motion."
    assert parsed.blocks[1].kind == "TABLE"
    assert parsed.blocks[1].heading_path == ("Strength Training",)
    assert "| Exercise | Sets |" in parsed.blocks[1].content
    assert "| Squat | 4 |" in parsed.blocks[1].content


def test_xlsx_parser_keeps_worksheet_as_a_header_preserving_table() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Beginner Plan"
    sheet.append(["Exercise", "Sets", "Reps"])
    sheet.append(["Squat", 4, 12])
    payload = BytesIO()
    workbook.save(payload)

    parsed = DocumentParserRegistry().parse(payload.getvalue(), file_name="plan.xlsx")

    block = parsed.blocks[0]
    assert block.kind == "TABLE"
    assert block.source_sheet == "Beginner Plan"
    assert block.heading_path == ("Beginner Plan",)
    assert "| Exercise | Sets | Reps |" in block.content
    assert "| Squat | 4 | 12 |" in block.content


def test_pdf_parser_reports_ocr_requirement_for_scanned_pdf() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    payload = BytesIO()
    writer.write(payload)

    with pytest.raises(DocumentParseError, match="OCR"):
        DocumentParserRegistry().parse(payload.getvalue(), file_name="scan.pdf")
