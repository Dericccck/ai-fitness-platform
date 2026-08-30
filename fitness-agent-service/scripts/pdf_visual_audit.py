"""渲染代表性 PDF 页面，并对解析结果执行结构化视觉审计。

这个脚本不是 OCR，也不是像素级截图比对工具。它把“原始页面看起来是什么样”和
“解析器认为这一页是什么类型”放在同一次可重复检查中：先用 Poppler 渲染页面，
再用当前解析器检查目录、表格、正文和图片密集页的路由。这样可以发现空白渲染、
页面范围错误或结构路由回归，同时把图片动作语义继续明确留给人工审核。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from app.rag.formats import DocumentParserRegistry, ParsedDocument, PdfPageProfile

VisualExpectation = Literal["text", "toc", "table", "visual"]


@dataclass(frozen=True)
class VisualCase:
    """一组可复现的页面样本和它应满足的解析契约。"""

    name: str
    relative_pdf: str
    pages: tuple[int, ...]
    expectation: VisualExpectation


# 这些样本覆盖当前健身知识库中最容易产生误判的页面类型。页面数量保持小而固定，
# 让开发机和 CI 都能快速运行；完整质量评测仍由 knowledge-quality-gate 负责。
DEFAULT_CASES: tuple[VisualCase, ...] = (
    VisualCase(
        name="chinese-article",
        relative_pdf="data/knowledge/raw/general-fitness/《全民健身指南》解读_国家体育总局.pdf",
        pages=(1,),
        expectation="text",
    ),
    VisualCase(
        name="formal-toc",
        relative_pdf="data/knowledge/raw/international-guidelines/Physical_Activity_Guidelines_2nd_edition.pdf",
        pages=(3,),
        expectation="toc",
    ),
    VisualCase(
        name="formal-table",
        relative_pdf="data/knowledge/raw/international-guidelines/Physical_Activity_Guidelines_2nd_edition.pdf",
        pages=(51, 52),
        expectation="table",
    ),
    VisualCase(
        name="who-table",
        relative_pdf="data/knowledge/raw/international-guidelines/WHO guidelines on physical activity and sedentary behaviour.pdf",
        pages=(29,),
        expectation="table",
    ),
    VisualCase(
        name="presentation-layout",
        relative_pdf="data/knowledge/raw/international-guidelines/Physical_Activity_Guidelines_2nd_edition_Presentation.pdf",
        pages=(4,),
        expectation="visual",
    ),
)


def page_ink_ratio(image_path: Path) -> float:
    """计算渲染页的非近白像素比例，用于发现 Poppler 输出空白页。"""

    with Image.open(image_path) as image:
        histogram = image.convert("L").histogram()
    total = sum(histogram)
    if total == 0:
        return 0.0
    # 245 以上视为纸张背景；不要求页面达到某个具体文字密度，避免误伤纯图片页。
    return sum(histogram[:245]) / total


def _blocks_on_page(parsed: ParsedDocument, page: int) -> list[object]:
    return [block for block in parsed.blocks if block.source_page == page]


def _profile_on_page(parsed: ParsedDocument, page: int) -> PdfPageProfile | None:
    if page < 1 or page > len(parsed.page_profiles):
        return None
    return parsed.page_profiles[page - 1]


def audit_page_expectation(
    parsed: ParsedDocument,
    page: int,
    expectation: VisualExpectation,
) -> tuple[list[str], bool]:
    """检查页面结构契约，返回硬错误和是否需要人工视觉复核。"""

    errors: list[str] = []
    profile = _profile_on_page(parsed, page)
    if profile is None:
        return [f"页面 {page} 不在解析结果的页码范围内"], False
    blocks = _blocks_on_page(parsed, page)
    visual_review = profile.route in {
        "VISUAL_REVIEW_REQUIRED",
        "OCR_AND_VISUAL_REVIEW_REQUIRED",
    }

    if expectation == "toc":
        if not profile.toc_detected:
            errors.append("页面没有被识别为目录页")
        if blocks:
            errors.append(f"目录页仍产生了 {len(blocks)} 个可索引块")
    elif expectation == "table":
        table_blocks = [block for block in blocks if getattr(block, "kind", None) == "TABLE"]
        if not table_blocks:
            errors.append("表格页没有产生 TABLE 内容块")
        for block in table_blocks:
            metadata = block.metadata or {}  # type: ignore[attr-defined]
            lines = block.content.splitlines()  # type: ignore[attr-defined]
            # 渲染器输出的 Markdown 表格允许单元格两侧有空格，例如 ``| --- |``；
            # 这里只验证确实存在分隔线，不把合法的空格误报成表格损坏。
            if len(lines) < 2 or "---" not in lines[1] or "|" not in lines[1]:
                errors.append("表格块缺少 Markdown 表头分隔行")
            for key in (
                "table_header_signature",
                "table_column_count",
                "table_continuation_status",
            ):
                if key not in metadata:
                    errors.append(f"表格块缺少 {key} 元数据")
            status = metadata.get("table_continuation_status")
            if status in {"AMBIGUOUS_REVIEW", "SHAPE_MISMATCH_REVIEW"}:
                errors.append(f"表格续接状态为 {status}，不能通过视觉回归门禁")
    elif expectation == "text":
        text_content = "\n".join(
            block.content for block in blocks if getattr(block, "kind", None) == "TEXT"
        )
        if not text_content.strip():
            errors.append("正文样本没有产生可索引文本")
        # 中文政府网站 PDF 常带模板导航；正文样本不应把这些模板重新带回 RAG。
        if "无障碍浏览" in text_content or "公务员邮箱" in text_content:
            errors.append("正文仍包含网站导航模板噪声")
    elif expectation == "visual":
        if profile.route not in {
            "VISUAL_REVIEW_REQUIRED",
            "OCR_AND_VISUAL_REVIEW_REQUIRED",
        }:
            errors.append(f"图片密集页没有进入视觉审核路由：{profile.route}")
        if profile.image_area_ratio < 0.45:
            errors.append(f"图片密集页占比异常偏低：{profile.image_area_ratio:.4f}")

    return errors, visual_review


def _render_page(
    pdftoppm: str,
    pdf_path: Path,
    page: int,
    output_dir: Path,
    case_name: str,
    dpi: int,
) -> Path:
    """只渲染指定页，避免为了审计少量样本生成整本 PDF 的中间文件。"""

    prefix = output_dir / f"{case_name}-page-{page:03d}"
    subprocess.run(
        [
            pdftoppm,
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-r",
            str(dpi),
            "-png",
            str(pdf_path),
            str(prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = prefix.with_suffix(".png")
    if not rendered.exists():
        raise RuntimeError(f"Poppler 未生成预期的渲染文件：{rendered}")
    return rendered


def run_audit(
    *,
    service_dir: Path,
    output_path: Path | None,
    dpi: int,
    keep_rendered: bool,
) -> int:
    """执行全部样本审计，并按质量门禁约定返回退出码。"""

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        print("未找到 pdftoppm，请安装 Poppler 后再执行 PDF 视觉审计", file=sys.stderr)
        return 2
    if dpi <= 0:
        print("dpi 必须是正整数", file=sys.stderr)
        return 2

    render_context = tempfile.TemporaryDirectory(prefix="fitness-pdf-visual-")
    render_dir = Path(render_context.name)
    if keep_rendered:
        render_context.cleanup()
        render_dir = Path(tempfile.mkdtemp(prefix="fitness-pdf-visual-"))

    result: dict[str, object] = {
        "schema_version": "2026-08-30.1",
        "audit": "pdf-rendered-visual-structural-audit",
        "dpi": dpi,
        "cases": [],
        "hard_failure_count": 0,
        "human_review_count": 0,
    }
    cases_result: list[dict[str, object]] = []

    try:
        for case in DEFAULT_CASES:
            pdf_path = service_dir / case.relative_pdf
            case_result: dict[str, object] = {
                "name": case.name,
                "source": case.relative_pdf,
                "pages": [],
                "expectation": case.expectation,
            }
            if not pdf_path.exists():
                case_result["errors"] = [f"来源文件不存在：{pdf_path}"]
                result["hard_failure_count"] = int(result["hard_failure_count"]) + 1
                cases_result.append(case_result)
                continue

            parsed = DocumentParserRegistry().parse(
                pdf_path.read_bytes(),
                file_name=pdf_path.name,
            )
            page_results: list[dict[str, object]] = []
            for page in case.pages:
                page_result: dict[str, object] = {"page": page}
                try:
                    rendered = _render_page(
                        pdftoppm,
                        pdf_path,
                        page,
                        render_dir,
                        case.name,
                        dpi,
                    )
                    ink_ratio = page_ink_ratio(rendered)
                    page_result["rendered_file"] = rendered.name
                    with Image.open(rendered) as image:
                        page_result["render_width"], page_result["render_height"] = image.size
                    page_result["ink_ratio"] = round(ink_ratio, 6)
                    if ink_ratio <= 0.001:
                        page_result.setdefault("errors", []).append("渲染结果接近空白页")
                except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
                    page_result.setdefault("errors", []).append(f"页面渲染失败：{exc}")

                profile = _profile_on_page(parsed, page)
                if profile is not None:
                    page_result["route"] = profile.route
                    page_result["toc_detected"] = profile.toc_detected
                    page_result["image_area_ratio"] = round(profile.image_area_ratio, 6)
                    page_result["native_text_chars"] = profile.native_text_chars
                    page_result["block_count"] = len(_blocks_on_page(parsed, page))
                errors, human_review = audit_page_expectation(parsed, page, case.expectation)
                if errors:
                    page_result.setdefault("errors", []).extend(errors)
                if human_review:
                    page_result["human_review_required"] = True
                    result["human_review_count"] = int(result["human_review_count"]) + 1
                if page_result.get("errors"):
                    result["hard_failure_count"] = int(result["hard_failure_count"]) + len(
                        page_result["errors"]  # type: ignore[arg-type]
                    )
                page_results.append(page_result)
            case_result["pages"] = page_results
            cases_result.append(case_result)
    finally:
        if not keep_rendered:
            render_context.cleanup()

    result["cases"] = cases_result
    if keep_rendered:
        result["render_directory"] = str(render_dir)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output_path is None:
        print(serialized, end="")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
        print(f"PDF 视觉审计报告已写入：{output_path}")
    print(
        f"PDF 视觉审计完成：硬错误 {result['hard_failure_count']}，"
        f"人工视觉复核页 {result['human_review_count']}"
    )
    return 1 if result["hard_failure_count"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染并审计代表性健身 PDF 页面")
    parser.add_argument("--output", type=Path, help="可选的 JSON 报告路径")
    parser.add_argument("--dpi", type=int, default=110, help="Poppler 渲染分辨率，默认 110")
    parser.add_argument(
        "--keep-rendered",
        action="store_true",
        help="保留渲染 PNG，便于人工复核；默认使用临时目录并在结束时清理",
    )
    args = parser.parse_args()
    service_dir = Path(__file__).resolve().parents[1]
    return run_audit(
        service_dir=service_dir,
        output_path=args.output,
        dpi=args.dpi,
        keep_rendered=args.keep_rendered,
    )


if __name__ == "__main__":
    raise SystemExit(main())
