"""知识上传审核与索引任务状态的持久化。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text

from app.infrastructure.database import Database

from .admin_models import (
    KnowledgeIngestionJob,
    KnowledgeJobNotFound,
    KnowledgeJobTransitionError,
    job_from_row,
)


class KnowledgeIngestionRepository:
    """在 PostgreSQL 中保持审核/任务状态转换原子且明确。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_job(
        self,
        *,
        job: KnowledgeIngestionJob,
    ) -> KnowledgeIngestionJob:
        """持久化待审核任务，并返回数据库标准化后的数据行。"""

        statement = text(
            """
            INSERT INTO knowledge_ingestion_jobs (
                id, source_uri, original_filename, storage_key, content_type, size_bytes,
                title, document_type, organization_id, owner_user_id, visibility, allowed_roles,
                effective_from, effective_to, requested_version, submitted_by, status,
                attempt_count, max_attempts, content_sha256, safety_status, scanner_name,
                malware_status, malware_scanner, malware_signature, malware_scanned_at
            ) VALUES (
                :id, :source_uri, :original_filename, :storage_key, :content_type, :size_bytes,
                :title, :document_type, :organization_id, :owner_user_id, :visibility,
                :allowed_roles, :effective_from, :effective_to, :requested_version,
                :submitted_by, 'PENDING_REVIEW', 0, :max_attempts, :content_sha256,
                :safety_status, :scanner_name, :malware_status, :malware_scanner,
                :malware_signature, :malware_scanned_at
            )
            RETURNING *
            """
        )
        params = _job_params(job)
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().one()
        return job_from_row(row)

    async def get_job(self, job_id: str) -> KnowledgeIngestionJob:
        """返回一个任务；不存在时抛出稳定的领域异常。"""

        statement = text("SELECT * FROM knowledge_ingestion_jobs WHERE id = :id")
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeJobNotFound("knowledge ingestion job was not found")
        return job_from_row(row)

    async def get_active_job_by_source(self, source_uri: str) -> KnowledgeIngestionJob | None:
        """查询同一来源是否已有未完成任务，避免批量导入重复提交。"""

        statement = text(
            """
            SELECT *
            FROM knowledge_ingestion_jobs
            WHERE source_uri = :source_uri
              AND status IN ('PENDING_REVIEW', 'QUEUED', 'INDEXING')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        async with self._database.engine.connect() as connection:
            row = (
                (await connection.execute(statement, {"source_uri": source_uri})).mappings().first()
            )
        return job_from_row(row) if row is not None else None

    async def list_jobs(
        self,
        *,
        organization_ids: frozenset[str] = frozenset(),
        platform_wide: bool = False,
        limit: int = 50,
    ) -> list[KnowledgeIngestionJob]:
        """只列出签名管理员身份被允许访问的任务范围。"""

        if limit < 1 or limit > 100:
            raise ValueError("job list limit must be between 1 and 100")
        if not platform_wide and not organization_ids:
            return []
        scope_clause = (
            "TRUE"
            if platform_wide
            else "visibility = 'ORGANIZATION' AND organization_id IN :organization_ids"
        )
        statement = text(
            f"""
            SELECT * FROM knowledge_ingestion_jobs
            WHERE {scope_clause}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ).bindparams(bindparam("organization_ids", expanding=True))
        params: dict[str, Any] = {
            "limit": limit,
            "organization_ids": sorted(organization_ids),
        }
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, params)).mappings().all()
        return [job_from_row(row) for row in rows]

    async def list_queued_ids(self, *, limit: int = 10) -> list[str]:
        """返回数量受限的排队任务 ID；每个 Worker 处理前仍需原子认领。"""

        if limit < 1 or limit > 100:
            raise ValueError("worker batch size must be between 1 and 100")
        statement = text(
            """
            SELECT id
            FROM knowledge_ingestion_jobs
            WHERE status = 'QUEUED'
            ORDER BY created_at
            LIMIT :limit
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, {"limit": limit})).mappings().all()
        return [str(row["id"]) for row in rows]

    async def approve(
        self, job_id: str, *, reviewer_id: str, comment: str | None
    ) -> KnowledgeIngestionJob:
        """将待审核任务准确地转入队列一次。"""

        return await self._review_transition(
            job_id,
            reviewer_id=reviewer_id,
            comment=comment,
            target_status="QUEUED",
        )

    async def reject(self, job_id: str, *, reviewer_id: str, comment: str) -> KnowledgeIngestionJob:
        """拒绝任务，但不删除暂存证据或审计状态。"""

        return await self._review_transition(
            job_id,
            reviewer_id=reviewer_id,
            comment=comment,
            target_status="REJECTED",
        )

    async def claim(self, job_id: str) -> KnowledgeIngestionJob | None:
        """认领排队任务，避免两个 Worker 同时为同一任务生成 Embedding。"""

        statement = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'INDEXING', attempt_count = attempt_count + 1,
                started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                error_code = NULL, error_message = NULL
            WHERE id = :id AND status = 'QUEUED'
            RETURNING *
            """
        )
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        return job_from_row(row) if row is not None else None

    async def complete(self, job_id: str, *, document_id: str) -> KnowledgeIngestionJob:
        """只有文档发布事务提交后，才将索引任务标记为成功。"""

        statement = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'SUCCEEDED', document_id = :document_id,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'INDEXING'
            RETURNING *
            """
        )
        return await self._transition_returning(
            statement, {"id": job_id, "document_id": document_id}
        )

    async def fail(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> KnowledgeIngestionJob:
        """持久化受限的失败信息；重试必须由管理员显式触发。"""

        statement = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'FAILED', error_code = :error_code, error_message = :error_message,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'INDEXING'
            RETURNING *
            """
        )
        return await self._transition_returning(
            statement,
            {
                "id": job_id,
                "error_code": error_code[:100],
                "error_message": error_message[:500],
            },
        )

    async def retry(self, job_id: str, *, reviewer_id: str) -> KnowledgeIngestionJob:
        """只有在有限重试预算未耗尽时，才重新排队失败任务。"""

        statement = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'QUEUED', reviewer_id = :reviewer_id,
                review_comment = 'manual retry', finished_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'FAILED' AND attempt_count < max_attempts
            RETURNING *
            """
        )
        return await self._transition_returning(
            statement, {"id": job_id, "reviewer_id": reviewer_id}
        )

    async def _review_transition(
        self,
        job_id: str,
        *,
        reviewer_id: str,
        comment: str | None,
        target_status: str,
    ) -> KnowledgeIngestionJob:
        statement = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = :target_status, reviewer_id = :reviewer_id,
                review_comment = :comment, reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'PENDING_REVIEW'
            RETURNING *
            """
        )
        return await self._transition_returning(
            statement,
            {
                "id": job_id,
                "reviewer_id": reviewer_id,
                "comment": comment,
                "target_status": target_status,
            },
        )

    async def _transition_returning(
        self, statement: Any, params: dict[str, Any]
    ) -> KnowledgeIngestionJob:
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().first()
        if row is None:
            raise KnowledgeJobTransitionError(
                "knowledge ingestion job state transition was rejected"
            )
        return job_from_row(row)


def _job_params(job: KnowledgeIngestionJob) -> dict[str, Any]:
    """将不可变领域对象转换为数据库驱动可安全使用的参数映射。"""

    return {
        "id": job.id,
        "source_uri": job.source_uri,
        "original_filename": job.original_filename,
        "storage_key": job.storage_key,
        "content_type": job.content_type,
        "size_bytes": job.size_bytes,
        "title": job.title,
        "document_type": job.document_type,
        "organization_id": job.organization_id,
        "owner_user_id": job.owner_user_id,
        "visibility": job.visibility,
        "allowed_roles": list(job.allowed_roles),
        "effective_from": job.effective_from,
        "effective_to": job.effective_to,
        "requested_version": job.requested_version,
        "submitted_by": job.submitted_by,
        "max_attempts": job.max_attempts,
        "content_sha256": job.content_sha256,
        "safety_status": job.safety_status,
        "scanner_name": job.scanner_name,
        "malware_status": job.malware_status,
        "malware_scanner": job.malware_scanner,
        "malware_signature": job.malware_signature,
        "malware_scanned_at": job.malware_scanned_at,
    }
