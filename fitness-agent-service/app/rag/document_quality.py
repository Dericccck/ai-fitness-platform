"""知识文档解析结果的离线质量指标和阈值校验。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .formats import ParsedBlock, PdfPageProfile
from .ingestion import ChunkDraft

_NOISE_MARKERS = (
    "无障碍浏览",
    "网站导航",
    "公务员邮箱",
    "请输入关键字",
    "打印此页",
    "关闭窗口",
    "版权所有",
    "table of contents",
)
_TABLE_SEPARATOR = re.compile(r"^\s*:?-{3,}:?\s*$")


@dataclass(frozen=True)
class DocumentQualityMetrics:
    """一份文档解析和父子切分结果的可比较质量指标。"""

    block_count: int
    noise_block_count: int
    fragment_block_count: int
    duplicate_block_count: int
    parent_count: int
    parent_complete_count: int
    table_count: int
    valid_table_count: int
    total_pages: int | None
    extracted_pages: int
    missing_pages: tuple[int, ...]
    ocr_required_pages: tuple[int, ...] = ()
    visual_review_required_pages: tuple[int, ...] = ()
    max_image_area_ratio: float = 0.0

    @property
    def noise_rate(self) -> float:
        """解析结果中疑似网页/目录噪声块的比例。"""

        return _ratio(self.noise_block_count, self.block_count)

    @property
    def fragment_rate(self) -> float:
        """排除标题、列表和表格后，异常短正文块的比例。"""

        eligible = self.block_count - self.table_count
        return _ratio(self.fragment_block_count, eligible)

    @property
    def duplicate_rate(self) -> float:
        """重复内容块占全部内容块的比例。"""

        return _ratio(self.duplicate_block_count, self.block_count)

    @property
    def parent_integrity(self) -> float:
        """拥有非空父节点且子节点能回溯到父节点的比例。"""

        return _ratio(self.parent_complete_count, self.parent_count)

    @property
    def table_integrity(self) -> float:
        """表格结构可验证的比例。没有表格时按 1.0 处理。"""

        return _ratio(self.valid_table_count, self.table_count, empty_value=1.0)

    @property
    def page_coverage(self) -> float | None:
        """有可追溯内容的 PDF 页面覆盖率；非 PDF 文档返回空值。"""

        if self.total_pages is None:
            return None
        return _ratio(self.extracted_pages, self.total_pages)

    def as_dict(self) -> dict[str, Any]:
        """转换为稳定 JSON 结构，供 CLI、CI 和后续审核看板复用。"""

        return {
            "block_count": self.block_count,
            "noise_block_count": self.noise_block_count,
            "noise_rate": round(self.noise_rate, 6),
            "fragment_block_count": self.fragment_block_count,
            "fragment_rate": round(self.fragment_rate, 6),
            "duplicate_block_count": self.duplicate_block_count,
            "duplicate_rate": round(self.duplicate_rate, 6),
            "parent_count": self.parent_count,
            "parent_complete_count": self.parent_complete_count,
            "parent_integrity": round(self.parent_integrity, 6),
            "table_count": self.table_count,
            "valid_table_count": self.valid_table_count,
            "table_integrity": round(self.table_integrity, 6),
            "total_pages": self.total_pages,
            "extracted_pages": self.extracted_pages,
            "missing_pages": list(self.missing_pages),
            "page_coverage": None if self.page_coverage is None else round(self.page_coverage, 6),
            "ocr_required_pages": list(self.ocr_required_pages),
            "visual_review_required_pages": list(self.visual_review_required_pages),
            "max_image_area_ratio": round(self.max_image_area_ratio, 6),
        }


@dataclass(frozen=True)
class DocumentQualityThresholds:
    """文档发布前的质量阈值；越权和检索指标由 RAG 评测门禁单独负责。"""

    max_noise_rate: float = 0.0
    max_fragment_rate: float = 0.35
    max_duplicate_rate: float = 0.02
    min_parent_integrity: float = 1.0
    min_table_integrity: float = 1.0
    max_missing_pages: int = 0
    max_ocr_required_pages: int = 0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> DocumentQualityThresholds:
        """从 JSON 配置读取阈值，并拒绝负数等明显错误配置。"""

        threshold = cls(
            max_noise_rate=float(data.get("max_noise_rate", cls.max_noise_rate)),
            max_fragment_rate=float(data.get("max_fragment_rate", cls.max_fragment_rate)),
            max_duplicate_rate=float(data.get("max_duplicate_rate", cls.max_duplicate_rate)),
            min_parent_integrity=float(data.get("min_parent_integrity", cls.min_parent_integrity)),
            min_table_integrity=float(data.get("min_table_integrity", cls.min_table_integrity)),
            max_missing_pages=int(data.get("max_missing_pages", cls.max_missing_pages)),
            max_ocr_required_pages=int(
                data.get("max_ocr_required_pages", cls.max_ocr_required_pages)
            ),
        )
        if not 0 <= threshold.max_noise_rate <= 1:
            raise ValueError("max_noise_rate must be between 0 and 1")
        if not 0 <= threshold.max_fragment_rate <= 1:
            raise ValueError("max_fragment_rate must be between 0 and 1")
        if not 0 <= threshold.max_duplicate_rate <= 1:
            raise ValueError("max_duplicate_rate must be between 0 and 1")
        if not 0 <= threshold.min_parent_integrity <= 1:
            raise ValueError("min_parent_integrity must be between 0 and 1")
        if not 0 <= threshold.min_table_integrity <= 1:
            raise ValueError("min_table_integrity must be between 0 and 1")
        if threshold.max_missing_pages < 0:
            raise ValueError("max_missing_pages must not be negative")
        if threshold.max_ocr_required_pages < 0:
            raise ValueError("max_ocr_required_pages must not be negative")
        return threshold

    def validate(self, metrics: DocumentQualityMetrics) -> list[str]:
        """返回阻断发布的具体原因，而不是只返回一个布尔值。"""

        failures: list[str] = []
        if metrics.noise_rate > self.max_noise_rate:
            failures.append(f"noise_rate {metrics.noise_rate:.4f} > {self.max_noise_rate:.4f}")
        if metrics.fragment_rate > self.max_fragment_rate:
            failures.append(
                f"fragment_rate {metrics.fragment_rate:.4f} > {self.max_fragment_rate:.4f}"
            )
        if metrics.duplicate_rate > self.max_duplicate_rate:
            failures.append(
                f"duplicate_rate {metrics.duplicate_rate:.4f} > {self.max_duplicate_rate:.4f}"
            )
        if metrics.parent_integrity < self.min_parent_integrity:
            failures.append(
                f"parent_integrity {metrics.parent_integrity:.4f} < {self.min_parent_integrity:.4f}"
            )
        if metrics.table_integrity < self.min_table_integrity:
            failures.append(
                f"table_integrity {metrics.table_integrity:.4f} < {self.min_table_integrity:.4f}"
            )
        if len(metrics.missing_pages) > self.max_missing_pages:
            failures.append(
                f"missing_pages {len(metrics.missing_pages)} > {self.max_missing_pages}"
            )
        if len(metrics.ocr_required_pages) > self.max_ocr_required_pages:
            failures.append(
                "ocr_required_pages "
                f"{len(metrics.ocr_required_pages)} > {self.max_ocr_required_pages}"
            )
        return failures


def measure_document_quality(
    blocks: tuple[ParsedBlock, ...] | list[ParsedBlock],
    drafts: list[ChunkDraft],
    *,
    total_pages: int | None = None,
    page_profiles: tuple[PdfPageProfile, ...] | list[PdfPageProfile] = (),
    short_block_chars: int = 80,
) -> DocumentQualityMetrics:
    """从解析块和父子切分草稿计算质量指标，不访问数据库。"""

    noise_count = sum(_contains_noise(block.content) for block in blocks)
    fragment_count = sum(
        _is_fragment(block.content, block.heading_path, short_block_chars)
        for block in blocks
        if block.kind == "TEXT"
    )
    duplicate_count = _duplicate_count(blocks)
    parent_count = len(drafts)
    parent_complete_count = sum(_draft_has_complete_parent(draft) for draft in drafts)
    tables = [block for block in blocks if block.kind == "TABLE"]
    valid_table_count = sum(_is_valid_markdown_table(block.content) for block in tables)
    extracted_pages = len({block.source_page for block in blocks if block.source_page is not None})
    missing_pages = (
        tuple(
            page
            for page in range(1, total_pages + 1)
            if page not in {block.source_page for block in blocks if block.source_page is not None}
        )
        if total_pages is not None
        else ()
    )
    ocr_required_pages = tuple(
        profile.page_number
        for profile in page_profiles
        if profile.route in {"OCR_REQUIRED", "OCR_AND_VISUAL_REVIEW_REQUIRED"}
    )
    visual_review_required_pages = tuple(
        profile.page_number
        for profile in page_profiles
        if profile.route in {"VISUAL_REVIEW_REQUIRED", "OCR_AND_VISUAL_REVIEW_REQUIRED"}
    )
    max_image_area_ratio = max(
        (profile.image_area_ratio for profile in page_profiles),
        default=0.0,
    )
    return DocumentQualityMetrics(
        block_count=len(blocks),
        noise_block_count=noise_count,
        fragment_block_count=fragment_count,
        duplicate_block_count=duplicate_count,
        parent_count=parent_count,
        parent_complete_count=parent_complete_count,
        table_count=len(tables),
        valid_table_count=valid_table_count,
        total_pages=total_pages,
        extracted_pages=extracted_pages,
        missing_pages=missing_pages,
        ocr_required_pages=ocr_required_pages,
        visual_review_required_pages=visual_review_required_pages,
        max_image_area_ratio=max_image_area_ratio,
    )


def _ratio(numerator: int, denominator: int, *, empty_value: float = 0.0) -> float:
    return numerator / denominator if denominator else empty_value


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized)


def _contains_noise(content: str) -> bool:
    normalized = _normalize(content)
    return any(_normalize(marker) in normalized for marker in _NOISE_MARKERS)


def _is_fragment(content: str, heading_path: tuple[str, ...], short_block_chars: int) -> bool:
    if heading_path or len(content.strip()) >= short_block_chars:
        return False
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    if not first_line:
        return True
    if first_line.startswith(("•", "▪", "- ", "* ")):
        return False
    if re.match(
        r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d+[、.)])", first_line
    ):
        return False
    if first_line.endswith(("。", "！", "？", ".", "!", "?", "；", ";")):
        return False
    # PDF/DOCX 中独立出现的短标题、附件编号和章节名不是碎片。
    if len(first_line) <= 20 and not any(mark in first_line for mark in "，,；;：:、"):
        return False
    return len(first_line) < 40


def _duplicate_count(blocks: tuple[ParsedBlock, ...] | list[ParsedBlock]) -> int:
    """只统计疑似正文重复，不把重复标题、表头和跨页合法建议算作异常。"""

    candidates = [
        block for block in blocks if block.kind == "TEXT" and len(block.content.strip()) >= 80
    ]
    grouped: dict[str, Counter[int | None]] = {}
    for block in candidates:
        grouped.setdefault(_normalize(block.content), Counter())[block.source_page] += 1
    # PDF 不同页面可能合法重复核心建议；只统计同页重复，或没有页码时的重复。
    duplicate_count = 0
    for page_counts in grouped.values():
        if None in page_counts:
            duplicate_count += max(0, page_counts[None] - 1)
        else:
            duplicate_count += sum(max(0, count - 1) for count in page_counts.values())
    return duplicate_count


def _compact(content: str) -> str:
    return re.sub(r"\s+", "", content)


def _draft_has_complete_parent(draft: ChunkDraft) -> bool:
    if not draft.content.strip() or not draft.parent_content.strip():
        return False
    if draft.table_index is not None:
        # 表格子节点会重复表头，父节点保存整张表；验证表头和至少一行数据能回溯即可。
        child_lines = [line.strip() for line in draft.content.splitlines() if line.strip()]
        parent = _compact(draft.parent_content)
        return len(child_lines) >= 2 and _compact(child_lines[0]) in parent
    return _compact(draft.content) in _compact(draft.parent_content)


def _is_valid_markdown_table(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < 2 or not all("|" in line for line in lines):
        return False
    separator_index = next(
        (index for index, line in enumerate(lines) if _is_table_separator_line(line)), None
    )
    if separator_index != 1:
        return False
    widths = [len(line.strip("|").split("|")) for line in lines]
    return len(set(widths)) == 1 and widths[0] > 0


def _is_table_separator_line(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEPARATOR.fullmatch(cell) for cell in cells)
