"""按资料清单安全批准非医疗资料，并复用生产索引流程发布知识版本。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from submit_knowledge_review import (
    MANIFEST_PATH,
    _build_review_report_builder,
    _source_uri,
)

from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.admin_models import KnowledgeIngestionJob, KnowledgeReviewReportNotFound
from app.rag.admin_repository import KnowledgeIngestionRepository
from app.rag.admin_service import KnowledgeAdminService
from app.rag.formats import DocumentParserRegistry, PdfOcrProvider
from app.rag.ingestion import DocumentIngestionService
from app.rag.ocr import HttpPdfOcrProvider
from app.rag.repository import KnowledgeRepository
from app.rag.safety import ClamAvScanner, CompositeDocumentScanner, StructuralDocumentScanner
from app.rag.service import RagService
from app.rag.storage import LocalDocumentStorage


def _build_parser(settings: Settings) -> DocumentParserRegistry:
    """按服务配置构建与 API 相同的文档解析器。"""

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
    """构建与 API 相同的文件安全扫描器；索引阶段不重复覆盖已有 verdict。"""

    malware = None
    if settings.rag_malware_scanner_backend == "clamav":
        malware = ClamAvScanner(
            settings.rag_clamav_host,
            port=settings.rag_clamav_port,
            timeout_seconds=settings.rag_clamav_timeout_seconds,
        )
    return CompositeDocumentScanner(StructuralDocumentScanner(), malware)


def _identity() -> AgentIdentity:
    """构造仅供本地受控脚本使用的超级管理员身份。"""

    now = int(datetime.now(UTC).timestamp())
    return AgentIdentity(
        subject="knowledge-review-cli",
        organization_ids=frozenset({"platform"}),
        roles=frozenset({"SUPER_ADMIN"}),
        issued_at=now,
        expires_at=now + 300,
    )


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    safe_sources = {
        _source_uri(entry)
        for entry in manifest["entries"]
        if bool(entry["indexable"]) and not bool(entry["requires_human_review"])
    }

    database = Database(settings)
    repository = KnowledgeRepository(database)
    jobs = KnowledgeIngestionRepository(database)
    models = ModelGateway(settings)
    parser = _build_parser(settings)
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
        _build_review_report_builder(settings),
        _build_scanner(settings),
        max_source_bytes=settings.rag_max_source_bytes,
        max_attempts=settings.rag_ingestion_max_attempts,
    )
    identity = _identity()
    report: list[dict[str, object]] = []
    try:
        all_jobs = await jobs.list_jobs(platform_wide=True, limit=100)
        for job in all_jobs:
            result = await _process_job(args, admin, identity, job, safe_sources)
            if result is not None:
                report.append(result)
    finally:
        await models.close()
        await database.close()
    print(json.dumps({"processed": report}, ensure_ascii=False, indent=2))
    return 0


async def _process_job(
    args: argparse.Namespace,
    admin: KnowledgeAdminService,
    identity: AgentIdentity,
    job: KnowledgeIngestionJob,
    safe_sources: set[str],
) -> dict[str, object] | None:
    """只处理清单明确允许自动审批的任务，其他任务原样保留。"""

    if job.source_uri not in safe_sources or job.status != "PENDING_REVIEW":
        return None
    try:
        review_report = await admin.get_review_report(identity, job.id)
    except KnowledgeReviewReportNotFound:
        # 0011 迁移之前创建的历史任务没有绑定解析证据，不能因清单看似安全就自动放行。
        return {"job_id": job.id, "title": job.title, "status": "REANALYSIS_REQUIRED"}
    if not review_report.can_admin_approve:
        return {
            "job_id": job.id,
            "title": job.title,
            "status": review_report.status,
            "required_review_domains": list(review_report.required_review_domains),
        }
    if args.dry_run:
        return {"job_id": job.id, "title": job.title, "status": "READY_TO_APPROVE"}
    approved = await admin.approve(
        identity,
        job.id,
        comment="清单标记为无需人工复核，按非医疗健身资料自动进入索引流程",
    )
    await admin.process_job(approved.id)
    return {"job_id": job.id, "title": job.title, "status": "APPROVED_AND_INDEXED"}


def main() -> int:
    """命令行入口；``--dry-run`` 只列出可批准任务，不改变数据库。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只列出可批准任务")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
