"""面向扫描型和部分扫描型 PDF 知识源的 HTTP OCR 适配器。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from .formats import DocumentParseError, ParsedBlock, ParsedDocument


class OcrServiceUnavailable(DocumentParseError):
    """已配置的 OCR 服务没有提供有效响应。"""


class HttpPdfOcrProvider:
    """调用独立 OCR 服务，并使用版本化、保留结构的契约。

    期望的响应结构：

        {
          "media_type": "application/pdf",
          "warnings": [],
          "blocks": [{"kind": "TEXT", "content": "...", "source_page": 1}]
        }

    OCR 服务有意独立于 Agent 进程。它可以使用托管 OCR 厂商或内部 GPU 部署，
    而不需要修改入库、父子分块、权限和引用代码。
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 20 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        if not endpoint_url:
            raise ValueError("OCR endpoint URL is required")
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._client = client

    def parse(
        self,
        content: bytes,
        *,
        file_name: str,
        pages: Sequence[int] = (),
    ) -> ParsedDocument:
        """提交原始 PDF，并在条件允许时只请求缺失页面。"""

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {"pages": ",".join(str(page) for page in pages)} if pages else {}
        files = {"file": (file_name, content, "application/pdf")}
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            response = client.post(self.endpoint_url, headers=headers, data=data, files=files)
            if response.status_code >= 400:
                raise DocumentParseError(f"OCR service returned HTTP {response.status_code}")
            if len(response.content) > self.max_response_bytes:
                raise DocumentParseError("OCR response exceeds the configured size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                raise DocumentParseError("OCR service returned invalid JSON") from exc
        except httpx.HTTPError as exc:
            raise OcrServiceUnavailable("OCR service is unavailable") from exc
        finally:
            if close_client:
                client.close()
        return _parsed_document_from_payload(payload)


def _parsed_document_from_payload(payload: Any) -> ParsedDocument:
    """在服务商输出进入分块和 Embedding 前进行校验。"""

    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise DocumentParseError("OCR response must contain a blocks array")
    blocks: list[ParsedBlock] = []
    for raw_block in payload["blocks"]:
        if not isinstance(raw_block, dict):
            raise DocumentParseError("OCR block must be an object")
        kind = raw_block.get("kind", "TEXT")
        content = raw_block.get("content")
        if kind not in {"TEXT", "TABLE"} or not isinstance(content, str) or not content.strip():
            raise DocumentParseError("OCR block has invalid kind or content")
        heading_path = raw_block.get("heading_path", [])
        if not isinstance(heading_path, list) or not all(
            isinstance(item, str) for item in heading_path
        ):
            raise DocumentParseError("OCR heading_path must be a string array")
        metadata = raw_block.get("metadata", {})
        if not isinstance(metadata, dict):
            raise DocumentParseError("OCR metadata must be an object")
        blocks.append(
            ParsedBlock(
                kind=kind,
                content=content,
                heading_path=tuple(heading_path),
                source_page=_optional_int(raw_block.get("source_page")),
                source_sheet=_optional_text(raw_block.get("source_sheet")),
                table_index=_optional_int(raw_block.get("table_index")),
                row_start=_optional_int(raw_block.get("row_start")),
                row_end=_optional_int(raw_block.get("row_end")),
                metadata={
                    str(key): value for key, value in metadata.items() if _safe_metadata(value)
                },
            )
        )
    if not blocks:
        raise DocumentParseError("OCR service returned no indexable blocks")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise DocumentParseError("OCR warnings must be a string array")
    media_type = payload.get("media_type", "application/pdf")
    if not isinstance(media_type, str):
        raise DocumentParseError("OCR media_type must be a string")
    return ParsedDocument(tuple(blocks), media_type, tuple(warnings))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentParseError("OCR coordinate must be an integer")
    return int(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 256:
        raise DocumentParseError("OCR source text metadata is invalid")
    return value


def _safe_metadata(value: Any) -> bool:
    return isinstance(value, (str, int, bool)) and not isinstance(value, float)
