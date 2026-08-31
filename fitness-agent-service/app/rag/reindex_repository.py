"""可复现知识索引重建批次的持久化仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text

from app.infrastructure.database import Database

from .admin_models import (
    KnowledgeJobNotFound,
    KnowledgeJobTransitionError,
    KnowledgeReindexItem,
    KnowledgeReindexJob,
    KnowledgeReindexSource,
    reindex_item_from_row,
    reindex_job_from_row,
)


class KnowledgeReindexRepository:
    """在 PostgreSQL 中保证批次级和文档级重建状态转换的原子性。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_sources(
        self,
        *,
        organization_id: str | None = None,
        document_id: str | None = None,
    ) -> list[KnowledgeReindexSource]:
        """为已发布文档创建快照，并关联不可变的暂存源文件。"""

        statement = text(
            """
            SELECT
                d.id AS document_id, d.source_uri, d.title, d.document_type,
                d.organization_id, c.owner_user_id, d.visibility,
                d.applicable_roles, d.effective_from, d.effective_to, d.version,
                c.storage_key, c.original_filename, c.content_type,
                c.approved_visual_pages
            FROM knowledge_documents d
            JOIN LATERAL (
                SELECT j.storage_key, j.original_filename, j.content_type,
                       j.owner_user_id, COALESCE(pc.approved_visual_pages, '{}')
                           AS approved_visual_pages
                FROM knowledge_ingestion_jobs j
                LEFT JOIN knowledge_publication_credentials pc
                  ON pc.job_id = j.id AND pc.revoked_at IS NULL
                WHERE j.document_id = d.id AND j.status = 'SUCCEEDED'
                ORDER BY j.finished_at DESC NULLS LAST, j.created_at DESC
                LIMIT 1
            ) c ON TRUE
            WHERE d.status = 'PUBLISHED'
              AND (:organization_id IS NULL OR d.organization_id = :organization_id)
              AND (:document_id IS NULL OR d.id = :document_id)
            ORDER BY d.id
            """
        )
        params = {"organization_id": organization_id, "document_id": document_id}
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, params)).mappings().all()
        return [_source_from_row(row) for row in rows]

    async def create_job(
        self,
        *,
        job: KnowledgeReindexJob,
        sources: Sequence[KnowledgeReindexSource],
        item_ids: Sequence[str],
    ) -> KnowledgeReindexJob:
        """在一个事务中持久化重建批次及其来源快照。"""

        if len(sources) != len(item_ids) or not sources:
            raise ValueError("重新索引任务必须为每个来源包含一个条目")
        job_statement = text(
            """
            INSERT INTO knowledge_reindex_jobs (
                id, requested_by, organization_id, target_document_id, status,
                total_documents, max_attempts
            ) VALUES (
                :id, :requested_by, :organization_id, :target_document_id,
                'QUEUED', :total_documents, :max_attempts
            )
            RETURNING *
            """
        )
        item_statement = text(
            """
            INSERT INTO knowledge_reindex_items (
                id, job_id, document_id, source_uri, title, document_type,
                organization_id, owner_user_id, visibility, allowed_roles,
                effective_from, effective_to, version, storage_key,
                original_filename, content_type, approved_visual_pages, status, max_attempts
            ) VALUES (
                :id, :job_id, :document_id, :source_uri, :title, :document_type,
                :organization_id, :owner_user_id, :visibility, :allowed_roles,
                :effective_from, :effective_to, :version, :storage_key,
                :original_filename, :content_type, :approved_visual_pages, 'PENDING', :max_attempts
            )
            """
        )
        job_params = {
            "id": job.id,
            "requested_by": job.requested_by,
            "organization_id": job.organization_id,
            "target_document_id": job.target_document_id,
            "total_documents": len(sources),
            "max_attempts": job.max_attempts,
        }
        item_params = [
            {
                "id": item_id,
                "job_id": job.id,
                "document_id": source.document_id,
                "source_uri": source.source_uri,
                "title": source.title,
                "document_type": source.document_type,
                "organization_id": source.organization_id,
                "owner_user_id": source.owner_user_id,
                "visibility": source.visibility,
                "allowed_roles": list(source.allowed_roles),
                "effective_from": source.effective_from,
                "effective_to": source.effective_to,
                "version": source.version,
                "storage_key": source.storage_key,
                "original_filename": source.original_filename,
                "content_type": source.content_type,
                "approved_visual_pages": list(source.approved_visual_pages),
                "max_attempts": job.max_attempts,
            }
            for item_id, source in zip(item_ids, sources, strict=True)
        ]
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(job_statement, job_params)).mappings().one()
            await connection.execute(item_statement, item_params)
        return reindex_job_from_row(row)

    async def get_job(self, job_id: str) -> KnowledgeReindexJob:
        statement = text("SELECT * FROM knowledge_reindex_jobs WHERE id = :id")
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeJobNotFound("未找到知识重新索引任务")
        return reindex_job_from_row(row)

    async def list_jobs(
        self,
        *,
        organization_ids: frozenset[str] = frozenset(),
        platform_wide: bool = False,
        limit: int = 50,
    ) -> list[KnowledgeReindexJob]:
        if limit < 1 or limit > 100:
            raise ValueError("重新索引任务列表限制必须在 1 到 100 之间")
        if not platform_wide and not organization_ids:
            return []
        scope_clause = "TRUE" if platform_wide else "organization_id IN :organization_ids"
        statement = text(
            f"""
            SELECT * FROM knowledge_reindex_jobs
            WHERE {scope_clause}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        params: dict[str, Any] = {
            "organization_ids": sorted(organization_ids),
            "limit": limit,
        }
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, params)).mappings().all()
        return [reindex_job_from_row(row) for row in rows]

    async def list_queued_ids(self, *, limit: int = 10) -> list[str]:
        if limit < 1 or limit > 100:
            raise ValueError("重新索引 Worker 批次大小必须在 1 到 100 之间")
        statement = text(
            """
            SELECT id FROM knowledge_reindex_jobs
            WHERE status = 'QUEUED'
            ORDER BY created_at
            LIMIT :limit
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, {"limit": limit})).mappings().all()
        return [str(row["id"]) for row in rows]

    async def claim_job(self, job_id: str) -> KnowledgeReindexJob | None:
        statement = text(
            """
            UPDATE knowledge_reindex_jobs
            SET status = 'INDEXING', attempt_count = attempt_count + 1,
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP, error_message = NULL
            WHERE id = :id AND status = 'QUEUED'
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        return reindex_job_from_row(row) if row is not None else None

    async def list_pending_item_ids(self, job_id: str, *, limit: int = 10) -> list[str]:
        statement = text(
            """
            SELECT id FROM knowledge_reindex_items
            WHERE job_id = :job_id AND status = 'PENDING'
            ORDER BY created_at
            LIMIT :limit
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (
                (await connection.execute(statement, {"job_id": job_id, "limit": limit}))
                .mappings()
                .all()
            )
        return [str(row["id"]) for row in rows]

    async def claim_item(self, item_id: str) -> KnowledgeReindexItem | None:
        statement = text(
            """
            UPDATE knowledge_reindex_items
            SET status = 'INDEXING', attempt_count = attempt_count + 1,
                started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                error_message = NULL
            WHERE id = :id AND status = 'PENDING'
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, {"id": item_id})).mappings().first()
        return reindex_item_from_row(row) if row is not None else None

    async def complete_item(self, item_id: str, *, skipped: bool) -> KnowledgeReindexItem:
        target_status = "SKIPPED" if skipped else "SUCCEEDED"
        statement = text(
            f"""
            UPDATE knowledge_reindex_items
            SET status = '{target_status}', finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'INDEXING'
            RETURNING *
            """
        )
        return await self._transition_item(statement, {"id": item_id})

    async def fail_item(self, item_id: str, *, error_message: str) -> KnowledgeReindexItem:
        statement = text(
            """
            UPDATE knowledge_reindex_items
            SET status = 'FAILED', error_message = :error_message,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'INDEXING'
            RETURNING *
            """
        )
        return await self._transition_item(
            statement, {"id": item_id, "error_message": error_message[:500]}
        )

    async def finalize_job(self, job_id: str) -> KnowledgeReindexJob:
        """根据项目状态计算计数器，只有全部项目结束后才关闭批次。"""

        statement = text(
            """
            WITH counts AS (
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('SUCCEEDED', 'SKIPPED')) AS processed,
                    COUNT(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded,
                    COUNT(*) FILTER (WHERE status = 'SKIPPED') AS skipped,
                    COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
                    COUNT(*) FILTER (WHERE status IN ('PENDING', 'INDEXING')) AS active
                FROM knowledge_reindex_items
                WHERE job_id = :id
            )
            UPDATE knowledge_reindex_jobs j
            SET processed_documents = counts.processed,
                succeeded_documents = counts.succeeded,
                skipped_documents = counts.skipped,
                failed_documents = counts.failed,
                status = CASE
                    WHEN counts.active > 0 THEN j.status
                    WHEN counts.failed > 0 THEN 'FAILED'
                    ELSE 'SUCCEEDED'
                END,
                error_message = CASE
                    WHEN counts.failed > 0 THEN 'one or more document rebuilds failed'
                    ELSE NULL
                END,
                finished_at = CASE
                    WHEN counts.active = 0 THEN CURRENT_TIMESTAMP
                    ELSE j.finished_at
                END,
                updated_at = CURRENT_TIMESTAMP
            FROM counts
            WHERE j.id = :id
            RETURNING j.*
            """
        )
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeJobNotFound("未找到知识重新索引任务")
        return reindex_job_from_row(row)

    async def retry(self, job_id: str) -> KnowledgeReindexJob:
        """只重新排队仍有重试额度的失败项目。"""

        item_statement = text(
            """
            UPDATE knowledge_reindex_items
            SET status = 'PENDING', finished_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id AND status = 'FAILED' AND attempt_count < max_attempts
            """
        )
        job_statement = text(
            """
            UPDATE knowledge_reindex_jobs
            SET status = 'QUEUED', finished_at = NULL, error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id AND status = 'FAILED'
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            result = await connection.execute(item_statement, {"job_id": job_id})
            if result.rowcount == 0:
                raise KnowledgeJobTransitionError("没有失败的重新索引条目可重试")
            row = (await connection.execute(job_statement, {"job_id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeJobTransitionError("重新索引任务未处于失败状态")
        return reindex_job_from_row(row)

    async def _transition_item(
        self, statement: Any, params: dict[str, Any]
    ) -> KnowledgeReindexItem:
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().first()
        if row is None:
            raise KnowledgeJobTransitionError("重新索引条目状态转换被拒绝")
        return reindex_item_from_row(row)


def _source_from_row(row: Any) -> KnowledgeReindexSource:
    return KnowledgeReindexSource(
        document_id=str(row["document_id"]),
        source_uri=str(row["source_uri"]),
        title=str(row["title"]),
        document_type=str(row["document_type"]),
        organization_id=str(row["organization_id"]) if row["organization_id"] else None,
        owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] else None,
        visibility=row["visibility"],
        allowed_roles=tuple(row["applicable_roles"] or ()),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        version=int(row["version"]),
        storage_key=str(row["storage_key"]),
        original_filename=str(row["original_filename"]),
        content_type=str(row["content_type"]),
        approved_visual_pages=tuple(int(page) for page in row["approved_visual_pages"] or ()),
    )
