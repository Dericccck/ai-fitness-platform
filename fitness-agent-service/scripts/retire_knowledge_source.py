"""将不适合检索的知识来源下线，并清理其派生索引数据。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import Settings
from app.infrastructure.database import Database
from app.rag.repository import KnowledgeRepository

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = SERVICE_ROOT / "data" / "knowledge" / "manifest.json"


def _source_uri(entry: dict[str, object]) -> str:
    """按批量导入命令相同的规则生成来源 URI。"""

    category = str(entry["category"]).replace("/", "-")
    return f"knowledge://fitness/{category}/{str(entry['sha256'])[:32]}"


def _find_entry(file_name: str) -> dict[str, object]:
    """只允许下线清单中明确标记为 reference-only 的文件。"""

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    matches = [entry for entry in manifest["entries"] if entry["file_name"] == file_name]
    if len(matches) != 1:
        raise ValueError(f"清单必须只包含一个名为 {file_name} 的文件")
    entry = matches[0]
    if bool(entry["indexable"]):
        raise ValueError("此命令只能下线不可索引的参考来源")
    return entry


async def _run(file_name: str, dry_run: bool) -> int:
    entry = _find_entry(file_name)
    source_uri = _source_uri(entry)
    if dry_run:
        print(json.dumps({"file_name": file_name, "source_uri": source_uri}, ensure_ascii=False))
        return 0

    database = Database(Settings())
    try:
        documents, parents, chunks = await KnowledgeRepository(database).archive_source(source_uri)
    finally:
        await database.close()
    print(
        json.dumps(
            {
                "file_name": file_name,
                "source_uri": source_uri,
                "archived_documents": documents,
                "deleted_parents": parents,
                "deleted_chunks": chunks,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    """命令行入口，默认执行下线；``--dry-run`` 只检查来源。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-name", required=True, help="manifest 中的精确文件名")
    parser.add_argument("--dry-run", action="store_true", help="只输出来源 URI，不修改数据库")
    args = parser.parse_args()
    return asyncio.run(_run(args.file_name, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
