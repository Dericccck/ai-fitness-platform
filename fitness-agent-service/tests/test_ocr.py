from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter

from app.rag.formats import DocumentParseError, DocumentParserRegistry
from app.rag.ocr import HttpPdfOcrProvider, OcrServiceUnavailable


def blank_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(output)
    return output.getvalue()


def test_http_ocr_provider_validates_structure_and_preserves_coordinates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "contract_version": "ocr-service-v1",
                "media_type": "application/pdf",
                "blocks": [
                    {
                        "kind": "TEXT",
                        "content": "OCR 热身",
                        "source_page": 1,
                        "heading_path": ["热身"],
                        "confidence": 0.96,
                        "source_region": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.3},
                    }
                ],
            },
        )

    provider = HttpPdfOcrProvider(
        "https://ocr.internal/v1/parse",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    parsed = provider.parse(blank_pdf(), file_name="scan.pdf", pages=(1,))

    assert parsed.blocks[0].source_page == 1
    assert parsed.blocks[0].heading_path == ("热身",)
    assert parsed.blocks[0].metadata == {
        "ocr_confidence_basis_points": 9600,
        "ocr_source_region_x_basis_points": 1000,
        "ocr_source_region_y_basis_points": 2000,
        "ocr_source_region_width_basis_points": 7000,
        "ocr_source_region_height_basis_points": 3000,
    }
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert b"1" in requests[0].content


def test_http_ocr_provider_rejects_malformed_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"blocks": "bad"}))
    )
    provider = HttpPdfOcrProvider("https://ocr.internal/v1/parse", client=client)

    with pytest.raises(DocumentParseError, match="blocks array"):
        provider.parse(blank_pdf(), file_name="scan.pdf")


def test_http_ocr_provider_rejects_unknown_contract_version() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "contract_version": "ocr-service-v2",
                    "blocks": [{"kind": "TEXT", "content": "OCR text", "source_page": 1}],
                },
            )
        )
    )
    provider = HttpPdfOcrProvider("https://ocr.internal/v1/parse", client=client)

    with pytest.raises(DocumentParseError, match="contract_version"):
        provider.parse(blank_pdf(), file_name="scan.pdf")


def test_http_ocr_provider_rejects_missing_confidence_or_region() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "contract_version": "ocr-service-v1",
                    "blocks": [{"kind": "TEXT", "content": "OCR text", "source_page": 1}],
                },
            )
        )
    )
    provider = HttpPdfOcrProvider("https://ocr.internal/v1/parse", client=client)

    with pytest.raises(DocumentParseError, match="confidence"):
        provider.parse(blank_pdf(), file_name="scan.pdf")


def test_http_ocr_provider_surfaces_transport_unavailability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = HttpPdfOcrProvider(
        "https://ocr.internal/v1/parse",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(OcrServiceUnavailable, match="unavailable"):
        provider.parse(blank_pdf(), file_name="scan.pdf")


def test_registry_requests_ocr_for_scanned_pdf() -> None:
    class FakeOcr:
        def __init__(self) -> None:
            self.pages: tuple[int, ...] = ()

        def parse(self, content: bytes, *, file_name: str, pages: tuple[int, ...] = ()):
            from app.rag.formats import ParsedBlock, ParsedDocument

            self.pages = pages
            return ParsedDocument(
                blocks=(
                    ParsedBlock(
                        kind="TEXT",
                        content="OCR text",
                        source_page=1,
                        metadata={
                            "ocr_confidence_basis_points": 9600,
                            "ocr_source_region_x_basis_points": 0,
                            "ocr_source_region_y_basis_points": 0,
                            "ocr_source_region_width_basis_points": 10000,
                            "ocr_source_region_height_basis_points": 10000,
                        },
                    ),
                ),
                media_type="application/pdf",
            )

    provider = FakeOcr()
    parsed = DocumentParserRegistry(pdf_ocr_provider=provider).parse(
        blank_pdf(), file_name="scan.pdf"
    )

    assert provider.pages == (1,)
    assert parsed.blocks[0].content == "OCR text"
    assert parsed.page_profiles[0].route == "NORMAL"
