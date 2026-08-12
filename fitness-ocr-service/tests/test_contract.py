from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import Settings
from app.engine import EngineStatus
from app.main import create_app


def _pdf_bytes(page_count: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeEngine:
    def status(self) -> EngineStatus:
        return EngineStatus(True, "fake")

    def predict(self, input_path: str):
        del input_path
        return [
            {
                "parsing_res_list": [
                    {"block_label": "title", "block_content": "训练计划"},
                    {"block_label": "text", "block_content": "训练前进行动态热身。"},
                    {
                        "block_label": "table",
                        "block_content": "<table><tr><th>动作</th><th>组数</th></tr>"
                        "<tr><td>深蹲</td><td>4</td></tr></table>",
                    },
                ]
            }
        ]


def _client() -> TestClient:
    settings = Settings(auth_required=False, max_pages=10)
    return TestClient(create_app(settings=settings, engine=FakeEngine()))


def test_live_and_ready() -> None:
    with _client() as client:
        assert client.get("/health/live").json() == {"status": "UP"}
        assert client.get("/health/ready").json() == {"status": "READY", "engine": "fake"}


def test_parse_preserves_contract_and_table_header() -> None:
    with _client() as client:
        response = client.post(
            "/v1/parse",
            files={"file": ("plan.pdf", _pdf_bytes(), "application/pdf")},
            data={"pages": "2"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["blocks"][0]["source_page"] == 2
    assert payload["blocks"][0]["heading_path"] == ["训练计划"]
    assert "| 动作 | 组数 |" in payload["blocks"][1]["content"]
    assert payload["blocks"][1]["table_index"] == 0


def test_invalid_pdf_is_rejected() -> None:
    with _client() as client:
        response = client.post(
            "/v1/parse",
            files={"file": ("plan.pdf", b"not-a-pdf", "application/pdf")},
        )
    assert response.status_code == 422


def test_authentication_is_required_when_enabled() -> None:
    settings = Settings(auth_required=True, api_key="secret")
    client = TestClient(create_app(settings=settings, engine=FakeEngine()))
    response = client.post(
        "/v1/parse",
        files={"file": ("plan.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 401
