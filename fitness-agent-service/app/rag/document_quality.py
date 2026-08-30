"""知识文档解析结果的离线质量指标和阈值校验。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
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

# 这些指标用于比较两次解析管线的变化。block_count、parent_count 等规模指标不直接
# 表示质量好坏，不能简单地因为数字变大或变小就判定回归。
_COMPARISON_METRICS = (
    "noise_rate",
    "fragment_rate",
    "duplicate_rate",
    "duplicate_glyph_block_count",
    "parent_integrity",
    "table_integrity",
    "page_coverage",
    "missing_pages",
    "ocr_required_pages",
    "table_ambiguous_continuation_count",
    "table_shape_mismatch_count",
)
_LOWER_IS_BETTER = frozenset(
    {
        "noise_rate",
        "fragment_rate",
        "duplicate_rate",
        "duplicate_glyph_block_count",
        "missing_pages",
        "ocr_required_pages",
        "table_ambiguous_continuation_count",
        "table_shape_mismatch_count",
    }
)
_HIGHER_IS_BETTER = frozenset({"parent_integrity", "table_integrity", "page_coverage"})
_STATUS_RANK = {"BLOCKED": 0, "REVIEW_REQUIRED": 1, "PASS": 2}


@dataclass(frozen=True)
class DocumentQualityMetrics:
    """一份文档解析和父子切分结果的可比较质量指标。"""

    block_count: int
    noise_block_count: int
    fragment_block_count: int
    duplicate_block_count: int
    duplicate_glyph_block_count: int
    parent_count: int
    parent_complete_count: int
    table_count: int
    valid_table_count: int
    total_pages: int | None
    extracted_pages: int
    missing_pages: tuple[int, ...]
    excluded_pages: tuple[int, ...] = ()
    ocr_required_pages: tuple[int, ...] = ()
    visual_review_required_pages: tuple[int, ...] = ()
    max_image_area_ratio: float = 0.0
    # 版面清洗证据只用于诊断和回归比较，不直接代表“越多越好/越少越好”。例如目录
    # 页数增加可能意味着识别能力变好，而断词修复次数增加也可能只是资料换了版本。
    toc_page_count: int = 0
    repeated_edge_line_count: int = 0
    dehyphenated_line_break_count: int = 0
    layout_reordered_page_count: int = 0
    table_continuation_count: int = 0
    table_ambiguous_continuation_count: int = 0
    table_shape_mismatch_count: int = 0

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
        # 目录页是有意排除的版式内容，不应被误报为“漏页”；真正的漏页是既没有
        # 正文/表格块、也没有明确排除原因的页面。
        return _ratio(self.extracted_pages + len(self.excluded_pages), self.total_pages)

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
            "duplicate_glyph_block_count": self.duplicate_glyph_block_count,
            "parent_count": self.parent_count,
            "parent_complete_count": self.parent_complete_count,
            "parent_integrity": round(self.parent_integrity, 6),
            "table_count": self.table_count,
            "valid_table_count": self.valid_table_count,
            "table_integrity": round(self.table_integrity, 6),
            "total_pages": self.total_pages,
            "extracted_pages": self.extracted_pages,
            "excluded_pages": list(self.excluded_pages),
            "missing_pages": list(self.missing_pages),
            "page_coverage": None if self.page_coverage is None else round(self.page_coverage, 6),
            "ocr_required_pages": list(self.ocr_required_pages),
            "visual_review_required_pages": list(self.visual_review_required_pages),
            "max_image_area_ratio": round(self.max_image_area_ratio, 6),
            "toc_page_count": self.toc_page_count,
            "repeated_edge_line_count": self.repeated_edge_line_count,
            "dehyphenated_line_break_count": self.dehyphenated_line_break_count,
            "layout_reordered_page_count": self.layout_reordered_page_count,
            "table_continuation_count": self.table_continuation_count,
            "table_ambiguous_continuation_count": self.table_ambiguous_continuation_count,
            "table_shape_mismatch_count": self.table_shape_mismatch_count,
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


def compare_quality_reports(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """比较两份同一批原始资料的质量报告，并显式报告回归。

    比较器只接受相同来源路径和相同 SHA-256 的报告。这样可以避免把“换了一批资料”
    误认为“解析器变好了”。比例类指标使用 ``after - before`` 计算增量；其中噪声、
    碎片、重复、缺页和 OCR 页数越小越好，父节点、表格和页面覆盖率越大越好。
    该函数不决定文档能否发布，发布仍由 ``DocumentQualityThresholds`` 负责。
    """

    before_entries = _report_entries(before)
    after_entries = _report_entries(after)
    before_paths = set(before_entries)
    after_paths = set(after_entries)
    if before_paths != after_paths:
        missing = sorted(before_paths - after_paths)
        added = sorted(after_paths - before_paths)
        raise ValueError(
            f"quality reports cover different sources: missing={missing}, added={added}"
        )

    entries: list[dict[str, Any]] = []
    for relative_path in sorted(before_paths):
        old_entry = before_entries[relative_path]
        new_entry = after_entries[relative_path]
        _ensure_same_source_hash(relative_path, old_entry, new_entry)
        old_metrics = _metrics_mapping(old_entry)
        new_metrics = _metrics_mapping(new_entry)
        deltas: dict[str, float | None] = {}
        improvements: list[str] = []
        regressions: list[str] = []
        for metric in _COMPARISON_METRICS:
            old_value = _metric_value(old_metrics, metric)
            new_value = _metric_value(new_metrics, metric)
            if old_value is None or new_value is None:
                deltas[metric] = None
                continue
            delta = new_value - old_value
            deltas[metric] = round(delta, 6)
            if abs(delta) <= 1e-9:
                continue
            if metric in _LOWER_IS_BETTER:
                (improvements if delta < 0 else regressions).append(metric)
            elif metric in _HIGHER_IS_BETTER:
                (improvements if delta > 0 else regressions).append(metric)

        old_status = str(old_entry.get("status", "UNKNOWN"))
        new_status = str(new_entry.get("status", "UNKNOWN"))
        status_regressed = (
            old_status in _STATUS_RANK
            and new_status in _STATUS_RANK
            and _STATUS_RANK[new_status] < _STATUS_RANK[old_status]
        )
        if status_regressed:
            regressions.append("status")
        entries.append(
            {
                "relative_path": relative_path,
                "before_status": old_status,
                "after_status": new_status,
                "status_changed": old_status != new_status,
                "metric_deltas": deltas,
                "improvements": improvements,
                "regressions": regressions,
            }
        )

    regressed_entries = [entry for entry in entries if entry["regressions"]]
    improved_entries = [entry for entry in entries if entry["improvements"]]
    unchanged_entries = [
        entry for entry in entries if not entry["improvements"] and not entry["regressions"]
    ]
    return {
        "schema_version": 1,
        "before_label": before.get("label", "before"),
        "after_label": after.get("label", "after"),
        "source_count": len(entries),
        "improved_count": len(improved_entries),
        "regressed_count": len(regressed_entries),
        "unchanged_count": len(unchanged_entries),
        "status_change_count": sum(entry["status_changed"] for entry in entries),
        "entries": entries,
    }


def _report_entries(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_entries = report.get("results")
    if not isinstance(raw_entries, list):
        raise TypeError("quality report results must be a list")
    entries: dict[str, Mapping[str, Any]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise TypeError("quality report entry must be an object")
        relative_path = raw_entry.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("quality report entry requires relative_path")
        if relative_path in entries:
            raise ValueError(f"quality report contains duplicate source: {relative_path}")
        entries[relative_path] = raw_entry
    return entries


def _ensure_same_source_hash(
    relative_path: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    before_hash = before.get("source_sha256")
    after_hash = after.get("source_sha256")
    if before_hash and after_hash and before_hash != after_hash:
        raise ValueError(f"source SHA-256 changed for {relative_path}")


def _metrics_mapping(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    metrics = entry.get("metrics", {})
    if not isinstance(metrics, Mapping):
        raise TypeError("quality report metrics must be an object")
    return metrics


def _metric_value(metrics: Mapping[str, Any], metric: str) -> float | None:
    value = metrics.get(metric)
    if metric in {"missing_pages", "ocr_required_pages"}:
        if not isinstance(value, list):
            return None
        return float(len(value))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"quality metric {metric} must be numeric or null")
    return float(value)


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
        and not _is_standalone_complete_parent_block(block)
        for block in blocks
        if block.kind == "TEXT"
    )
    duplicate_count = _duplicate_count(blocks)
    duplicate_glyph_count = sum(_contains_duplicate_glyphs(block.content) for block in blocks)
    parent_count = len(drafts)
    parent_complete_count = sum(_draft_has_complete_parent(draft) for draft in drafts)
    tables = [block for block in blocks if block.kind == "TABLE"]
    valid_table_count = sum(
        _is_valid_markdown_table(block.content) and not _table_has_continuation_issue(block)
        for block in tables
    )
    extracted_page_set = {block.source_page for block in blocks if block.source_page is not None}
    excluded_pages = tuple(
        sorted(profile.page_number for profile in page_profiles if profile.toc_detected)
    )
    extracted_pages = len(extracted_page_set)
    missing_pages = (
        tuple(
            page
            for page in range(1, total_pages + 1)
            if page not in extracted_page_set and page not in excluded_pages
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
        duplicate_glyph_block_count=duplicate_glyph_count,
        parent_count=parent_count,
        parent_complete_count=parent_complete_count,
        table_count=len(tables),
        valid_table_count=valid_table_count,
        total_pages=total_pages,
        extracted_pages=extracted_pages,
        missing_pages=missing_pages,
        excluded_pages=excluded_pages,
        ocr_required_pages=ocr_required_pages,
        visual_review_required_pages=visual_review_required_pages,
        max_image_area_ratio=max_image_area_ratio,
        toc_page_count=sum(profile.toc_detected for profile in page_profiles),
        repeated_edge_line_count=sum(
            profile.removed_repeated_edge_lines for profile in page_profiles
        ),
        dehyphenated_line_break_count=sum(
            profile.dehyphenated_line_breaks for profile in page_profiles
        ),
        layout_reordered_page_count=sum(profile.detected_columns > 1 for profile in page_profiles),
        table_continuation_count=sum(
            (block.metadata or {}).get("table_continuation_status")
            in {"CONTINUATION_START", "CONTINUATION"}
            for block in tables
        ),
        table_ambiguous_continuation_count=sum(
            (block.metadata or {}).get("table_continuation_status") == "AMBIGUOUS_REVIEW"
            for block in tables
        ),
        table_shape_mismatch_count=sum(
            (block.metadata or {}).get("table_continuation_status") == "SHAPE_MISMATCH_REVIEW"
            for block in tables
        ),
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


def _contains_duplicate_glyphs(content: str) -> bool:
    """检测字体映射导致的整词重复字形，规则与 PDF 清洗器保持一致。

    这里仅用于质量观测，不负责修复内容；修复仍由 PDF 解析器执行。把它单独记录下来，
    才能在解析器升级前后证明噪声确实下降，而不是只看到总体状态没有变化。
    """

    for match in re.finditer(r"[A-Za-z0-9]+", content):
        token = match.group(0)
        if (
            len(token) >= 4
            and len(token) % 2 == 0
            and all(token[index] == token[index + 1] for index in range(0, len(token), 2))
        ):
            return True
    return False


def _compact(content: str) -> str:
    return re.sub(r"\s+", "", content)


def _is_standalone_complete_parent_block(block: ParsedBlock) -> bool:
    """识别标题自身就是完整父节点的结构块，避免把它误计为正文碎片。"""

    parent_content = block.parent_content
    if not parent_content:
        return False
    return _compact(block.content) == _compact(parent_content)


def _draft_has_complete_parent(draft: ChunkDraft) -> bool:
    if not draft.content.strip() or not draft.parent_content.strip():
        return False
    if draft.table_index is not None:
        # 表格子节点会重复表头，父节点保存整张表；验证表头和至少一行数据能回溯即可。
        child_lines = [line.strip() for line in draft.content.splitlines() if line.strip()]
        parent = _compact(draft.parent_content)
        return len(child_lines) >= 2 and _compact(child_lines[0]) in parent
    child = _compact(draft.content)
    parent = _compact(draft.parent_content)
    if draft.heading_path:
        # 分块器会把章节路径注入子节点正文，方便独立检索；父节点只把章节路径
        # 作为一处前缀保存，所以不能要求“带前缀的整段子节点”在父节点中连续出现。
        # 去掉这个系统注入的前缀后再验证正文可回溯，避免把正常的父子结构误判为损坏。
        heading_prefix = _compact(" / ".join(draft.heading_path))
        child = child.removeprefix(heading_prefix)
    if not child:
        return False
    if child in parent:
        return True
    # 复杂双栏 PDF 的父文本和子文本可能来自同一批文字，但列流顺序不同，导致
    # 字符串不再连续。此时只接受“子块中的每个有意义词项都能在父节点找到”的
    # 保守回退，不能用模糊相似度直接放行缺字或幻觉内容。
    token_source = draft.content.casefold()
    if draft.heading_path:
        token_source = token_source.removeprefix(" / ".join(draft.heading_path).casefold())
    child_tokens = re.findall(r"[A-Za-z0-9\u3400-\u9fff]{2,}", token_source)
    parent_normalized = draft.parent_content.casefold()
    if not child_tokens:
        return False
    token_coverage = sum(token in parent_normalized for token in child_tokens) / len(child_tokens)
    return token_coverage >= 0.98


def _table_has_continuation_issue(block: ParsedBlock) -> bool:
    """判断表格是否存在无法自动确认的跨页续接风险。"""

    return (block.metadata or {}).get("table_continuation_status") in {
        "AMBIGUOUS_REVIEW",
        "SHAPE_MISMATCH_REVIEW",
    }


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
