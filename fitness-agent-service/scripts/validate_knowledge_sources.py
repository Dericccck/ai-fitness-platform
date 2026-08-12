"""批量验证本地健身资料是否能被当前 RAG 解析器读取。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.rag.formats import (
    DocumentParseError,
    DocumentParserRegistry,
    UnsupportedDocumentFormatError,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = SERVICE_ROOT / "data" / "knowledge" / "manifest.json"


def sha256(path: Path) -> str:
    """分块计算当前文件哈希，用于发现清单生成后的本地文件替换。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_entry(registry: DocumentParserRegistry, entry: dict[str, Any]) -> dict[str, Any]:
    """校验单个清单条目，并保留解析器返回的页码、表格和警告信息。"""

    path = SERVICE_ROOT / str(entry["relative_path"]).removeprefix("fitness-agent-service/")
    result: dict[str, Any] = {
        "relative_path": entry["relative_path"],
        "status": "PASS",
        "indexable": bool(entry["indexable"]),
        "warnings": [],
    }

    if not entry["indexable"]:
        result["status"] = "REFERENCE_ONLY"
        result["warnings"] = ["该格式当前只保留人工参考，不进入 RAG 索引"]
        return result

    try:
        current_sha256 = sha256(path)
    except OSError as exc:
        result["status"] = "FAIL"
        result["error"] = str(exc)
        return result
    result["hash_match"] = current_sha256 == entry["sha256"]
    if not result["hash_match"]:
        result["status"] = "FAIL"
        result["error"] = "文件哈希与 manifest 不一致，请重新生成清单"
        return result

    try:
        parsed = registry.parse(path.read_bytes(), file_name=path.name)
    except (DocumentParseError, UnsupportedDocumentFormatError, OSError) as exc:
        result["status"] = "FAIL"
        result["error"] = str(exc)
        return result

    result.update(
        {
            "block_count": len(parsed.blocks),
            "text_block_count": sum(block.kind == "TEXT" for block in parsed.blocks),
            "table_block_count": sum(block.kind == "TABLE" for block in parsed.blocks),
            "extractable_page_count": len(
                {block.source_page for block in parsed.blocks if block.source_page is not None}
            ),
            "pages": sorted(
                {block.source_page for block in parsed.blocks if block.source_page is not None}
            ),
            "warnings": list(parsed.warnings),
        }
    )
    if not parsed.blocks:
        result["status"] = "FAIL"
        result["error"] = "解析结果没有可索引内容"
    elif parsed.warnings:
        result["status"] = "PASS_WITH_WARNINGS"
        result["needs_ocr_or_manual_review"] = True
    return result


def main() -> int:
    """读取清单并输出机器可读及人类可读的验证摘要。"""

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = DocumentParserRegistry()
    results = [validate_entry(registry, entry) for entry in manifest["entries"]]
    summary = {
        "manifest": str(MANIFEST_PATH.relative_to(SERVICE_ROOT)),
        "total": len(results),
        "pass": sum(item["status"] == "PASS" for item in results),
        "pass_with_warnings": sum(item["status"] == "PASS_WITH_WARNINGS" for item in results),
        "reference_only": sum(item["status"] == "REFERENCE_ONLY" for item in results),
        "fail": sum(item["status"] == "FAIL" for item in results),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
