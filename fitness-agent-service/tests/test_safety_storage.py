from io import BytesIO
from zipfile import ZipFile

import pytest

from app.rag.formats import DocumentParserRegistry, ParsedBlock, ParsedDocument
from app.rag.safety import DocumentSafetyError, StructuralDocumentScanner
from app.rag.storage import DocumentStorageError, LocalDocumentStorage


def test_structural_scanner_returns_content_identity_for_text() -> None:
    result = StructuralDocumentScanner().scan("guide.md", b"# Warmup")

    assert len(result.sha256) == 64
    assert result.status == "STRUCTURAL_VALIDATED"
    assert result.scanner_name == "structural-v1"


def test_structural_scanner_rejects_invalid_pdf_and_zip_traversal(tmp_path) -> None:
    scanner = StructuralDocumentScanner()
    with pytest.raises(DocumentSafetyError, match="signature"):
        scanner.scan("guide.pdf", b"not-a-pdf")

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with pytest.raises(DocumentSafetyError, match="unsafe paths"):
        scanner.scan("guide.docx", payload.getvalue())


def test_local_storage_rejects_arbitrary_read_paths(tmp_path) -> None:
    storage = LocalDocumentStorage(str(tmp_path))
    key = storage.store("job-1", "guide.md", b"# Guide", content_type="text/markdown")

    assert storage.read(key) == b"# Guide"
    with pytest.raises(DocumentStorageError):
        storage.read("../outside.md")


def test_pdf_parser_can_receive_an_injected_ocr_provider() -> None:
    from pypdf import PdfWriter

    payload = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(payload)

    class FakeOcr:
        def parse(self, content: bytes, *, file_name: str) -> ParsedDocument:
            return ParsedDocument(
                blocks=(ParsedBlock(kind="TEXT", content="OCR warmup", source_page=1),),
                media_type="application/pdf",
            )

    parsed = DocumentParserRegistry(pdf_ocr_provider=FakeOcr()).parse(
        payload.getvalue(), file_name="scan.pdf"
    )

    assert parsed.blocks[0].content == "OCR warmup"
