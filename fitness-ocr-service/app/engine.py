"""PaddleOCR 适配器与标准结果转换。"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol

from .config import Settings
from .models import OcrBlock, OcrSourceRegion


class OcrEngineUnavailable(RuntimeError):
    """推理运行时或模型无法加载。"""


class OcrEngineError(RuntimeError):
    """推理引擎返回了不可使用的结果。"""


class DocumentEngine(Protocol):
    """服务和测试使用的最小推理引擎边界。"""

    def status(self) -> EngineStatus:
        """返回底层模型运行时是否已就绪。"""

    def predict(self, input_path: str) -> Iterable[Any]:
        """为每个已处理页面返回一个 Paddle 风格的结果。"""


@dataclass(frozen=True)
class EngineStatus:
    """对外暴露的就绪状态，不泄露模型内部细节。"""

    ready: bool
    engine_name: str
    error: str | None = None


class PaddleStructureEngine:
    """运行 PaddleOCR PP-StructureV3，只暴露经过清洗的页面结果。

    首次检查就绪状态时才执行导入和模型构建。这样没有安装 Paddle 的机器仍可运行契约测试，
    同时生产环境在真实模型可用前会保持未就绪。
    """

    name = "paddleocr-pp-structurev3"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pipeline: Any | None = None
        self._load_error: str | None = None

    def status(self) -> EngineStatus:
        """只加载一次模型，并返回安全的就绪状态摘要。"""

        if self._pipeline is not None:
            return EngineStatus(True, self.name)
        if self._load_error is not None:
            return EngineStatus(False, self.name, self._load_error)
        try:
            from paddleocr import PPStructureV3  # type: ignore[import-not-found]

            # 集中管理模型构建，后续可以替换为 PaddleOCR-VL 或云端适配器，
            # 不需要修改 HTTP 契约。
            self._pipeline = PPStructureV3(
                lang=self.settings.language,
                device=self.settings.device,
                use_doc_orientation_classify=self.settings.use_doc_orientation_classify,
                use_doc_unwarping=self.settings.use_doc_unwarping,
                use_textline_orientation=self.settings.use_textline_orientation,
                use_table_recognition=self.settings.use_table_recognition,
                use_formula_recognition=self.settings.use_formula_recognition,
                format_block_content=self.settings.format_block_content,
            )
        except Exception as exc:  # noqa: BLE001 - 不同模型运行时可能抛出不同异常。
            self._load_error = f"PaddleOCR 引擎不可用：{exc}"
            return EngineStatus(False, self.name, self._load_error)
        return EngineStatus(True, self.name)

    def predict(self, input_path: str) -> Iterator[Any]:
        """确认模型就绪后返回原始 Paddle 结果。"""

        status = self.status()
        if not status.ready or self._pipeline is None:
            raise OcrEngineUnavailable(status.error or "OCR 引擎不可用")
        try:
            yield from self._pipeline.predict(input_path)
        except Exception as exc:
            raise OcrEngineError(f"PaddleOCR 推理失败：{exc}") from exc


def result_to_mapping(result: Any) -> Mapping[str, Any]:
    """将 Paddle 结果对象转换为类似 JSON 的映射。"""

    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, Mapping):
        return payload
    raise OcrEngineError("OCR 引擎返回了非对象结果")


def blocks_from_page_result(
    result: Any,
    *,
    source_page: int,
    table_index_start: int,
    page_width: float,
    page_height: float,
) -> tuple[list[OcrBlock], int]:
    """将一个 Paddle 页面转换为带来源坐标的契约内容块。"""

    payload = result_to_mapping(result)
    raw_blocks = payload.get("parsing_res_list", [])
    if not isinstance(raw_blocks, list):
        raise OcrEngineError("OCR 页面结果的 parsing_res_list 必须是数组")

    blocks: list[OcrBlock] = []
    heading_path: list[str] = []
    table_index = table_index_start
    page_confidence = _page_confidence(payload)
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping):
            raise OcrEngineError("OCR 页面块必须是对象")
        label = _safe_text(raw_block.get("block_label"), "text").lower()
        content = _safe_text(raw_block.get("block_content"), "").strip()
        if not content:
            continue

        is_table = label in {"table", "table_caption"} or "<table" in content.lower()
        if is_table:
            normalized = html_table_to_markdown(content)
            if not normalized:
                continue
            confidence = _block_confidence(raw_block, page_confidence)
            source_region = _block_source_region(
                raw_block,
                page_width=page_width,
                page_height=page_height,
            )
            blocks.append(
                OcrBlock(
                    kind="TABLE",
                    content=normalized,
                    heading_path=list(heading_path),
                    source_page=source_page,
                    confidence=confidence,
                    source_region=source_region,
                    table_index=table_index,
                    metadata={"ocr_engine": "paddleocr-pp-structurev3", "block_label": label},
                )
            )
            table_index += 1
            continue

        if label in {"title", "doc_title", "section_header", "header"}:
            heading_path = [content]
            continue
        confidence = _block_confidence(raw_block, page_confidence)
        source_region = _block_source_region(
            raw_block,
            page_width=page_width,
            page_height=page_height,
        )
        blocks.append(
            OcrBlock(
                kind="TEXT",
                content=content,
                heading_path=list(heading_path),
                source_page=source_page,
                confidence=confidence,
                source_region=source_region,
                metadata={"ocr_engine": "paddleocr-pp-structurev3", "block_label": label},
            )
        )
    return blocks, table_index


def _page_confidence(payload: Mapping[str, Any]) -> float | None:
    """从 Paddle 页面级 OCR 结果提取兜底置信度。"""

    for key in ("confidence", "score", "ocr_score"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return float(value)
    overall = payload.get("overall_ocr_res")
    if isinstance(overall, Mapping):
        scores = _as_sequence(overall.get("rec_scores"))
        if scores is None:
            scores = _as_sequence(overall.get("scores"))
        if scores is not None:
            valid = [score for score in _numeric_values(scores) if 0 <= score <= 1]
            if valid:
                return sum(valid) / len(valid)
    return None


def _block_confidence(raw_block: Mapping[str, Any], page_confidence: float | None) -> float:
    """提取块级置信度；没有可追溯分数时 fail-closed。"""

    for key in ("confidence", "score", "block_score", "ocr_score", "rec_score"):
        value = raw_block.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return float(value)
    if page_confidence is not None:
        return page_confidence
    raise OcrEngineError("OCR 块缺少置信度分数")


def _block_source_region(
    raw_block: Mapping[str, Any],
    *,
    page_width: float,
    page_height: float,
) -> OcrSourceRegion:
    """将 Paddle 像素坐标框转成 Agent 契约要求的归一化区域。"""

    if page_width <= 0 or page_height <= 0:
        raise OcrEngineError("PDF 页面尺寸必须为正数")
    raw_box = next(
        (
            raw_block.get(key)
            for key in ("block_bbox", "bbox", "block_box", "box")
            if raw_block.get(key) is not None
        ),
        None,
    )
    raw_coordinates = _as_sequence(raw_box)
    if raw_coordinates is None or len(raw_coordinates) < 4:
        raise OcrEngineError("OCR 块缺少源边界框")
    coordinates = _numeric_values(raw_coordinates)
    if len(coordinates) < 4:
        raise OcrEngineError("OCR 块源边界框无效")
    if len(coordinates) >= 8:
        x0, y0 = min(coordinates[0::2]), min(coordinates[1::2])
        x1, y1 = max(coordinates[0::2]), max(coordinates[1::2])
    else:
        x0, y0, x1, y1 = coordinates[:4]
    if x1 <= x0 or y1 <= y0:
        raise OcrEngineError("OCR 块源边界框尺寸无效")
    return OcrSourceRegion(
        x=round(max(0.0, x0 / page_width), 6),
        y=round(max(0.0, y0 / page_height), 6),
        width=round(min(1.0, x1 / page_width) - max(0.0, x0 / page_width), 6),
        height=round(min(1.0, y1 / page_height) - max(0.0, y0 / page_height), 6),
    )


def _as_sequence(value: Any) -> list[Any] | None:
    """把 Paddle/NumPy 的数组型结果安全转换为普通列表。

    PaddleOCR 的不同版本和不同推理后端可能把识别分数、四边形坐标返回为
    ``list``、``tuple`` 或带 ``tolist()`` 方法的 NumPy 数组。这里仅接受一维
    可枚举结果，不直接导入 NumPy，避免让 OCR 服务的契约转换层绑定某个运行时。
    """

    if isinstance(value, (list, tuple)):
        return list(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            converted = tolist()
        except Exception:  # noqa: BLE001 - 第三方数组对象可能在转换时抛出任意异常。
            return None
        if isinstance(converted, list):
            return converted
    return None


def _numeric_values(value: Any) -> list[float]:
    """递归展开一维或二维数组中的数值，兼容四边形坐标的嵌套形式。"""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_numeric_values(item))
        return flattened
    return []


def _safe_text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


class _TableParser(HTMLParser):
    """用于读取 Paddle 表格内容块的轻量级无依赖 HTML 表格解析器。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_row is not None:
            self._current_row.append(" ".join(self._current_cell or []).strip())
            self._current_cell = None
        elif lowered == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(html.unescape(data))


def html_table_to_markdown(content: str) -> str:
    """Markdown 内容保持不变，其他内容转换为 Paddle HTML 表格输出。"""

    if "<table" not in content.lower():
        return re.sub(r"\s+", " ", content).strip()
    parser = _TableParser()
    parser.feed(content)
    rows = [row for row in parser.rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)
