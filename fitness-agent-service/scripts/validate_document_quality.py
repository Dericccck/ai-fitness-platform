"""执行本地知识源的解析、父子切分和页面完整性质量门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from app.rag.document_quality import DocumentQualityThresholds, measure_document_quality
from app.rag.formats import DocumentParseError, DocumentParserRegistry
from app.rag.ingestion import chunk_parsed_blocks

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = SERVICE_ROOT / "data" / "knowledge" / "manifest.json"
DEFAULT_THRESHOLDS_PATH = SERVICE_ROOT / "evals" / "document_quality_thresholds.json"


def _sha256(path: Path) -> str:
    """计算文件哈希，确保质量报告对应清单中的原始文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _total_pdf_pages(path: Path) -> int | None:
    """读取 PDF 总页数；其他格式不强行伪造页码覆盖率。"""

    if path.suffix.lower() != ".pdf":
        return None
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def _source_path(relative_path: str) -> Path:
    """解析清单路径并拒绝越界文件。"""

    path = (SERVICE_ROOT / relative_path.removeprefix("fitness-agent-service/")).resolve()
    if SERVICE_ROOT not in path.parents:
        raise ValueError(f"source path escapes service root: {relative_path}")
    return path


def _validate_entry(
    registry: DocumentParserRegistry,
    thresholds: DocumentQualityThresholds,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """生成单个来源的质量结果；任何失败只阻断该来源，不吞掉原因。"""

    result: dict[str, Any] = {
        "relative_path": entry["relative_path"],
        "indexable": bool(entry["indexable"]),
        "status": "REFERENCE_ONLY" if not entry["indexable"] else "PASS",
        "failures": [],
        "warnings": [],
    }
    if not entry["indexable"]:
        result["warnings"] = ["清单标记为仅供参考，不进入索引"]
        return result
    try:
        path = _source_path(str(entry["relative_path"]))
        if _sha256(path) != str(entry["sha256"]):
            result["status"] = "BLOCKED"
            result["failures"] = ["文件哈希与 manifest 不一致"]
            return result
        parsed = registry.parse(path.read_bytes(), file_name=path.name)
        drafts = chunk_parsed_blocks(parsed.blocks, max_chunk_chars=1200, overlap_chars=120)
        metrics = measure_document_quality(
            parsed.blocks,
            drafts,
            total_pages=_total_pdf_pages(path),
            page_profiles=parsed.page_profiles,
        )
        failures = thresholds.validate(metrics)
        result.update(
            {
                "metrics": metrics.as_dict(),
                "page_profiles": [profile.as_dict() for profile in parsed.page_profiles],
                "failures": failures,
                "warnings": list(parsed.warnings),
            }
        )
        if failures:
            result["status"] = "BLOCKED"
        elif (
            parsed.warnings
            or metrics.visual_review_required_pages
            or entry.get("requires_human_review")
        ):
            result["status"] = "REVIEW_REQUIRED"
        else:
            result["status"] = "PASS"
    except (DocumentParseError, OSError, ValueError) as exc:
        result["status"] = "BLOCKED"
        result["failures"] = [str(exc)]
    return result


def main(argv: list[str] | None = None) -> int:
    """输出 JSON 质量报告；存在 BLOCKED 来源时返回非零退出码。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    thresholds = DocumentQualityThresholds.from_mapping(
        json.loads(args.thresholds.read_text(encoding="utf-8"))
    )
    registry = DocumentParserRegistry()
    results = [_validate_entry(registry, thresholds, entry) for entry in manifest["entries"]]
    summary = {
        "manifest": str(args.manifest),
        "thresholds": str(args.thresholds),
        "total": len(results),
        "pass": sum(item["status"] == "PASS" for item in results),
        "review_required": sum(item["status"] == "REVIEW_REQUIRED" for item in results),
        "reference_only": sum(item["status"] == "REFERENCE_ONLY" for item in results),
        "blocked": sum(item["status"] == "BLOCKED" for item in results),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # REVIEW_REQUIRED 也不能作为“可自动发布”通过，只能等待人工审核或 OCR 复核。
    return 1 if summary["blocked"] or summary["review_required"] else 0


if __name__ == "__main__":
    sys.exit(main())
