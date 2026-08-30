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
                    {
                        "block_label": "text",
                        "block_content": "训练前进行动态热身。",
                        "confidence": 0.98,
                        "block_bbox": [10, 10, 90, 30],
                    },
                    {
                        "block_label": "table",
                        "block_content": "<table><tr><th>动作</th><th>组数</th></tr>"
                        "<tr><td>深蹲</td><td>4</td></tr></table>",
                        "confidence": 0.96,
                        "block_bbox": [10, 35, 90, 90],
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
    assert payload["contract_version"] == "ocr-service-v1"
    assert payload["blocks"][0]["source_page"] == 2
    assert payload["blocks"][0]["heading_path"] == ["训练计划"]
    assert payload["blocks"][0]["confidence"] == 0.98
    assert payload["blocks"][0]["source_region"] == {
        "x": 0.1,
        "y": 0.1,
        "width": 0.8,
        "height": 0.2,
    }
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


def test_parse_rejects_engine_output_without_confidence_or_region() -> None:
    class IncompleteEngine(FakeEngine):
        def predict(self, input_path: str):
            del input_path
            return [
                {"parsing_res_list": [{"block_label": "text", "block_content": "缺少审计证据"}]}
            ]

    settings = Settings(auth_required=False, max_pages=10)
    client = TestClient(create_app(settings=settings, engine=IncompleteEngine()))

    response = client.post(
        "/v1/parse",
        files={"file": ("plan.pdf", _pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 503


def test_parse_accepts_array_like_scores_and_bounding_boxes() -> None:
    class ArrayLike:
        def __init__(self, values: list[float]) -> None:
            self.values = values

        def tolist(self) -> list[float]:
            return list(self.values)

    class NestedArrayLike:
        def tolist(self) -> list[list[float]]:
            return [[10, 10], [90, 10], [90, 30], [10, 30]]

    class ArrayEngine(FakeEngine):
        def predict(self, input_path: str):
            del input_path
            return [
                {
                    "overall_ocr_res": {"rec_scores": ArrayLike([0.91, 0.95])},
                    "parsing_res_list": [
                        {
                            "block_label": "text",
                            "block_content": "数组型结果",
                            "block_bbox": NestedArrayLike(),
                        }
                    ],
                }
            ]

    with TestClient(
        create_app(settings=Settings(auth_required=False), engine=ArrayEngine())
    ) as client:
        response = client.post(
            "/v1/parse",
            files={"file": ("plan.pdf", _pdf_bytes(1), "application/pdf")},
        )

    assert response.status_code == 200
    block = response.json()["blocks"][0]
    assert block["confidence"] == 0.9299999999999999
    assert block["source_region"]["width"] == 0.8
