"""面向扫描型和部分扫描型 PDF 知识源的 HTTP OCR 适配器。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import httpx

from .formats import (
    DocumentParseError,
    ParsedBlock,
    ParsedDocument,
    PdfPageProfile,
    PdfPageRoute,
)

OCR_CONTRACT_VERSION = "ocr-service-v1"


class OcrServiceUnavailable(DocumentParseError):
    """已配置的 OCR 服务没有提供有效响应。"""


class HttpPdfOcrProvider:
    """调用独立 OCR 服务，并使用版本化、保留结构的契约。

    期望的响应结构：

        {
          "contract_version": "ocr-service-v1",
          "media_type": "application/pdf",
          "warnings": [],
          "blocks": [{
            "kind": "TEXT", "content": "...", "source_page": 1,
            "confidence": 0.96,
            "source_region": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.3}
          }]
        }

    OCR 服务有意独立于 Agent 进程。它可以使用托管 OCR 厂商或内部 GPU 部署，
    而不需要修改入库、父子分块、权限和引用代码。置信度和归一化来源区域是必填字段，
    因为没有这两项证据时不能解除 OCR 阻断，也不能准确回溯引用位置。
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
            raise ValueError("必须提供 OCR endpoint URL")
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
                raise DocumentParseError(f"OCR 服务返回 HTTP {response.status_code}")
            if len(response.content) > self.max_response_bytes:
                raise DocumentParseError("OCR 响应超过配置的大小限制")
            try:
                payload = response.json()
            except ValueError as exc:
                raise DocumentParseError("OCR 服务返回了无效 JSON") from exc
        except httpx.HTTPError as exc:
            raise OcrServiceUnavailable("OCR 服务不可用") from exc
        finally:
            if close_client:
                client.close()
        return _parsed_document_from_payload(payload, requested_pages=tuple(pages))


def _parsed_document_from_payload(
    payload: Any,
    *,
    requested_pages: Sequence[int] = (),
) -> ParsedDocument:
    """在服务商输出进入分块和 Embedding 前进行校验。"""

    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise DocumentParseError("OCR 响应必须包含 blocks 数组")
    if payload.get("contract_version") != OCR_CONTRACT_VERSION:
        raise DocumentParseError("OCR 响应包含不支持的 contract_version")
    blocks: list[ParsedBlock] = []
    for raw_block in payload["blocks"]:
        if not isinstance(raw_block, dict):
            raise DocumentParseError("OCR 块必须是对象")
        kind = raw_block.get("kind", "TEXT")
        content = raw_block.get("content")
        if kind not in {"TEXT", "TABLE"} or not isinstance(content, str) or not content.strip():
            raise DocumentParseError("OCR 块的 kind 或 content 无效")
        heading_path = raw_block.get("heading_path", [])
        if not isinstance(heading_path, list) or not all(
            isinstance(item, str) for item in heading_path
        ):
            raise DocumentParseError("OCR heading_path 必须是字符串数组")
        metadata = raw_block.get("metadata", {})
        if not isinstance(metadata, dict):
            raise DocumentParseError("OCR metadata 必须是对象")
        source_page = _required_positive_int(raw_block.get("source_page"))
        if requested_pages and source_page not in set(requested_pages):
            raise DocumentParseError("OCR 块的 source_page 不在请求的页面范围内")
        confidence_basis_points = _confidence_basis_points(raw_block.get("confidence"))
        region_metadata = _source_region_metadata(raw_block.get("source_region"))
        blocks.append(
            ParsedBlock(
                kind=kind,
                content=content,
                heading_path=tuple(heading_path),
                source_page=source_page,
                source_sheet=_optional_text(raw_block.get("source_sheet")),
                table_index=_optional_int(raw_block.get("table_index")),
                row_start=_optional_int(raw_block.get("row_start")),
                row_end=_optional_int(raw_block.get("row_end")),
                metadata={
                    **{str(key): value for key, value in metadata.items() if _safe_metadata(value)},
                    "ocr_confidence_basis_points": confidence_basis_points,
                    **region_metadata,
                },
            )
        )
    if not blocks:
        raise DocumentParseError("OCR 服务没有返回可索引的块")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise DocumentParseError("OCR warnings 必须是字符串数组")
    media_type = payload.get("media_type", "application/pdf")
    if not isinstance(media_type, str):
        raise DocumentParseError("OCR media_type 必须是字符串")
    page_profiles = _page_profiles_from_payload(payload.get("page_profiles", []))
    return ParsedDocument(tuple(blocks), media_type, tuple(warnings), page_profiles)


def _page_profiles_from_payload(payload: Any) -> tuple[PdfPageProfile, ...]:
    """校验可选 OCR 页面画像，拒绝服务商伪造未知路由或越界比例。"""

    if not isinstance(payload, list):
        raise DocumentParseError("OCR page_profiles 必须是数组")
    profiles: list[PdfPageProfile] = []
    allowed_routes = {
        "NORMAL",
        "OCR_REQUIRED",
        "VISUAL_REVIEW_REQUIRED",
        "OCR_AND_VISUAL_REVIEW_REQUIRED",
    }
    for raw in payload:
        if not isinstance(raw, dict):
            raise DocumentParseError("OCR 页面配置必须是对象")
        raw_route = raw.get("route", "NORMAL")
        if not isinstance(raw_route, str) or raw_route not in allowed_routes:
            raise DocumentParseError("OCR 页面配置包含无效 route")
        route = cast(PdfPageRoute, raw_route)
        image_area_ratio = _bounded_ratio(raw.get("image_area_ratio", 0))
        text_area_ratio = _bounded_ratio(raw.get("text_area_ratio", 0))
        reasons = raw.get("reasons", [])
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise DocumentParseError("OCR 页面配置的 reasons 必须是字符串数组")
        profiles.append(
            PdfPageProfile(
                page_number=_required_positive_int(raw.get("page_number")),
                image_count=_non_negative_int(raw.get("image_count", 0)),
                image_area_ratio=image_area_ratio,
                native_text_chars=_non_negative_int(raw.get("native_text_chars", 0)),
                text_area_ratio=text_area_ratio,
                table_count=_non_negative_int(raw.get("table_count", 0)),
                caption_count=_non_negative_int(raw.get("caption_count", 0)),
                route=route,
                reasons=tuple(reason[:128] for reason in reasons),
            )
        )
    return tuple(profiles)


def _bounded_ratio(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentParseError("OCR 页面配置的 ratio 必须是数值")
    ratio = float(value)
    if not 0 <= ratio <= 1:
        raise DocumentParseError("OCR 页面配置的 ratio 必须在 0 到 1 之间")
    return ratio


def _confidence_basis_points(value: Any) -> int:
    """将 OCR 服务的小数置信度转换为稳定的整数基点，避免浮点进入元数据。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentParseError("OCR 块的 confidence 必须是数字")
    confidence = float(value)
    if not 0 <= confidence <= 1:
        raise DocumentParseError("OCR 块的 confidence 必须在 0 到 1 之间")
    return round(confidence * 10_000)


def _source_region_metadata(value: Any) -> dict[str, int]:
    """校验页面归一化区域，并转换成 0-10000 基点保存。"""

    if not isinstance(value, dict):
        raise DocumentParseError("OCR 块的 source_region 必须是对象")
    coordinates = {key: _bounded_ratio(value.get(key)) for key in ("x", "y", "width", "height")}
    if coordinates["width"] <= 0 or coordinates["height"] <= 0:
        raise DocumentParseError("OCR 块的 source_region 尺寸必须为正数")
    if coordinates["x"] + coordinates["width"] > 1:
        raise DocumentParseError("OCR 块的 source_region 超过页面宽度")
    if coordinates["y"] + coordinates["height"] > 1:
        raise DocumentParseError("OCR 块的 source_region 超过页面高度")
    return {
        f"ocr_source_region_{key}_basis_points": round(number * 10_000)
        for key, number in coordinates.items()
    }


def _required_positive_int(value: Any) -> int:
    parsed = _non_negative_int(value)
    if parsed < 1:
        raise DocumentParseError("OCR 页码必须为正数")
    return parsed


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DocumentParseError("OCR 页面配置数量必须是非负整数")
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentParseError("OCR 坐标必须是整数")
    return int(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 256:
        raise DocumentParseError("OCR 源文本元数据无效")
    return value


def _safe_metadata(value: Any) -> bool:
    return isinstance(value, (str, int, bool)) and not isinstance(value, float)
