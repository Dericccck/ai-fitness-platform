"""将本地健身资料提交为待审核任务，不绕过管理员发布和索引流程。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.admin_models import KnowledgeUploadMetadata
from app.rag.admin_repository import KnowledgeIngestionRepository
from app.rag.admin_service import KnowledgeAdminService
from app.rag.formats import DocumentParserRegistry, PdfOcrProvider
from app.rag.ingestion import DocumentIngestionService
from app.rag.ocr import HttpPdfOcrProvider
from app.rag.repository import KnowledgeRepository
from app.rag.safety import ClamAvScanner, CompositeDocumentScanner, StructuralDocumentScanner
from app.rag.service import RagService
from app.rag.storage import LocalDocumentStorage

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = SERVICE_ROOT / "data" / "knowledge" / "manifest.json"


def _sha256(path: Path) -> str:
    """分块计算文件哈希，防止清单生成后文件被替换。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_path(relative_path: str) -> Path:
    """将清单相对路径解析到服务目录，并拒绝越界路径。"""

    relative = relative_path.removeprefix("fitness-agent-service/")
    path = (SERVICE_ROOT / relative).resolve()
    if SERVICE_ROOT not in path.parents:
        raise ValueError(f"knowledge source escapes service root: {relative_path}")
    return path


def _source_uri(entry: dict[str, object]) -> str:
    """使用分类和内容哈希生成稳定、不含用户输入路径的来源 URI。"""

    category = str(entry["category"]).replace("/", "-")
    return f"knowledge://fitness/{category}/{str(entry['sha256'])[:32]}"


def _build_parser(settings: Settings) -> DocumentParserRegistry:
    """按服务配置决定是否调用独立 OCR 服务。"""

    provider: PdfOcrProvider | None = None
    if settings.rag_ocr_backend == "http":
        provider = HttpPdfOcrProvider(
            settings.rag_ocr_endpoint_url,
            api_key=settings.rag_ocr_api_key,
            timeout_seconds=settings.rag_ocr_timeout_seconds,
            max_response_bytes=settings.rag_ocr_max_response_bytes,
        )
    return DocumentParserRegistry(
        max_source_bytes=settings.rag_max_source_bytes,
        pdf_ocr_provider=provider,
    )


def _build_scanner(settings: Settings) -> CompositeDocumentScanner:
    """结构检查始终执行；配置 ClamAV 时再叠加真实杀毒 verdict。"""

    malware = None
    if settings.rag_malware_scanner_backend == "clamav":
        malware = ClamAvScanner(
            settings.rag_clamav_host,
            port=settings.rag_clamav_port,
            timeout_seconds=settings.rag_clamav_timeout_seconds,
        )
    return CompositeDocumentScanner(StructuralDocumentScanner(), malware)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    parser = _build_parser(settings)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if args.dry_run:
        report = [_dry_run_entry(parser, entry) for entry in manifest["entries"]]
        print(json.dumps({"submitted": report}, ensure_ascii=False, indent=2))
        return 0

    database = Database(settings)
    repository = KnowledgeRepository(database)
    jobs = KnowledgeIngestionRepository(database)
    models = ModelGateway(settings)
    rag = RagService(
        repository,
        models,
        RerankerClient(settings),
        candidate_limit=settings.rag_candidate_limit,
        keyword_candidate_limit=settings.rag_keyword_candidate_limit,
        top_k=settings.rag_top_k,
        embedding_batch_size=settings.rag_embedding_batch_size,
        embedding_dimensions=settings.embedding_dimensions,
        vector_weight=settings.rag_vector_weight,
        keyword_weight=settings.rag_keyword_weight,
        rrf_k=settings.rag_rrf_k,
    )
    ingestion = DocumentIngestionService(
        repository,
        rag,
        max_chunk_chars=settings.rag_chunk_max_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
        parser_registry=parser,
    )
    admin = KnowledgeAdminService(
        jobs,
        repository,
        ingestion,
        LocalDocumentStorage(settings.rag_staging_dir),
        parser,
        _build_scanner(settings),
        max_source_bytes=settings.rag_max_source_bytes,
        max_attempts=settings.rag_ingestion_max_attempts,
    )
    identity = AgentIdentity(
        subject="knowledge-seed-cli",
        organization_ids=frozenset({"platform"}),
        roles=frozenset({"SUPER_ADMIN"}),
        issued_at=int(datetime.now(UTC).timestamp()),
        expires_at=int(datetime.now(UTC).timestamp()) + 300,
    )
    report: list[dict[str, object]] = []
    try:
        for entry in manifest["entries"]:
            item = await _submit_entry(args, admin, jobs, identity, parser, entry)
            report.append(item)
    finally:
        await models.close()
        await database.close()
    print(json.dumps({"submitted": report}, ensure_ascii=False, indent=2))
    return 0


def _dry_run_entry(parser: DocumentParserRegistry, entry: dict[str, object]) -> dict[str, object]:
    """只做本地文件检查，dry-run 不连接数据库也不初始化模型。"""

    relative_path = str(entry["relative_path"])
    path = _source_path(relative_path)
    item: dict[str, object] = {"file_name": str(entry["file_name"]), "status": "SKIPPED"}
    if not bool(entry["indexable"]):
        item["reason"] = "REFERENCE_ONLY"
        return item
    if not path.is_file():
        item.update(status="BLOCKED", reason="SOURCE_NOT_FOUND")
        return item
    if _sha256(path) != str(entry["sha256"]):
        item.update(status="BLOCKED", reason="HASH_MISMATCH")
        return item
    try:
        parsed = parser.parse(path.read_bytes(), file_name=path.name)
    except Exception as exc:  # noqa: BLE001 - 报告单份资料错误，继续处理其他来源
        item.update(status="BLOCKED", reason="PARSE_FAILED", detail=str(exc)[:300])
        return item
    if parsed.warnings:
        item.update(status="BLOCKED", reason="OCR_OR_MANUAL_REVIEW", warnings=list(parsed.warnings))
        return item
    item.update(status="READY_FOR_REVIEW", source_uri=_source_uri(entry))
    return item


async def _submit_entry(
    args: argparse.Namespace,
    admin: KnowledgeAdminService,
    jobs: KnowledgeIngestionRepository,
    identity: AgentIdentity,
    parser: DocumentParserRegistry,
    entry: dict[str, object],
) -> dict[str, object]:
    """校验并提交一个来源；警告来源保持在报告中，不自动进入审核队列。"""

    relative_path = str(entry["relative_path"])
    path = _source_path(relative_path)
    item: dict[str, object] = {"file_name": str(entry["file_name"]), "status": "SKIPPED"}
    if not bool(entry["indexable"]):
        item["reason"] = "REFERENCE_ONLY"
        return item
    if not path.is_file():
        item.update(status="BLOCKED", reason="SOURCE_NOT_FOUND")
        return item
    if _sha256(path) != str(entry["sha256"]):
        item.update(status="BLOCKED", reason="HASH_MISMATCH")
        return item
    try:
        parsed = parser.parse(path.read_bytes(), file_name=path.name)
    except Exception as exc:  # noqa: BLE001 - 报告单份资料错误，继续处理其他来源
        item.update(status="BLOCKED", reason="PARSE_FAILED", detail=str(exc)[:300])
        return item
    if parsed.warnings:
        item.update(status="BLOCKED", reason="OCR_OR_MANUAL_REVIEW", warnings=list(parsed.warnings))
        return item

    source_uri = _source_uri(entry)
    existing = await jobs.get_active_job_by_source(source_uri)
    if existing is not None:
        item.update(status="ALREADY_PENDING", job_id=existing.id)
        return item
    metadata = KnowledgeUploadMetadata(
        source_uri=source_uri,
        title=Path(str(entry["file_name"])).stem,
        document_type=str(entry["document_type"]),
        organization_id=None,
        visibility="GLOBAL",
        allowed_roles=tuple(str(role) for role in entry["allowed_roles"]),
        effective_from=datetime.now(UTC),
        effective_to=None,
    )
    if args.dry_run:
        item.update(status="READY_FOR_REVIEW", source_uri=source_uri)
        return item
    job = await admin.submit_upload(
        identity,
        file_name=path.name,
        content_type=str(entry["media_type"]),
        content=path.read_bytes(),
        metadata=metadata,
    )
    item.update(status="PENDING_REVIEW", job_id=job.id, source_uri=source_uri)
    return item


def main() -> int:
    """命令行入口；默认执行真实提交，``--dry-run`` 只生成候选报告。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只校验并输出候选，不写入数据库")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
