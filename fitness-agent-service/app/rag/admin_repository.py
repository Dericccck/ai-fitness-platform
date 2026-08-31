"""知识上传审核与索引任务状态的持久化。"""

from __future__ import annotations

import json
from secrets import token_hex
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import Database

from .admin_models import (
    KnowledgeIngestionJob,
    KnowledgeJobNotFound,
    KnowledgeJobTransitionError,
    KnowledgeReviewReportNotFound,
    job_from_row,
)
from .formats import PdfPageProfile
from .review import KnowledgeReviewFinding, KnowledgeReviewReport
from .review_workflow import (
    KnowledgePublicationCredential,
    KnowledgeReviewDecision,
    KnowledgeReviewOutcome,
    KnowledgeReviewRegion,
    approved_visual_pages,
)


class KnowledgeIngestionRepository:
    """在 PostgreSQL 中保持审核/任务状态转换原子且明确。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_job(
        self,
        *,
        job: KnowledgeIngestionJob,
        review_report: KnowledgeReviewReport,
    ) -> KnowledgeIngestionJob:
        """在同一事务中持久化待审核任务和不可变的首版解析报告。"""

        if review_report.job_id != job.id:
            raise ValueError("审查报告必须绑定到已创建的摄取任务")
        if review_report.document_sha256 != job.content_sha256:
            raise ValueError("审查报告哈希必须与暂存文档哈希匹配")
        if review_report.report_version != 1:
            raise ValueError("第一版审查报告的版本必须为 1")

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
        review_statement = text(
            """
            INSERT INTO knowledge_review_reports (
                id, job_id, report_version, document_sha256, parser_name, parser_version,
                parser_pipeline_version, review_policy_version, media_type,
                declared_risk_level, source_requires_human_review,
                status,
                quality_metrics, page_profiles, warnings, findings, required_review_domains,
                recommended_reviewer_roles, required_qualifications
            ) VALUES (
                :id, :job_id, :report_version, :document_sha256, :parser_name, :parser_version,
                :parser_pipeline_version, :review_policy_version, :media_type,
                :declared_risk_level, :source_requires_human_review, :status,
                CAST(:quality_metrics AS JSONB), CAST(:page_profiles AS JSONB), :warnings,
                CAST(:findings AS JSONB), :required_review_domains,
                :recommended_reviewer_roles, :required_qualifications
            )
            """
        )
        params = _job_params(job)
        async with self._database.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().one()
            await connection.execute(review_statement, _review_report_params(review_report))
        return job_from_row(row)

    async def get_latest_review_report(self, job_id: str) -> KnowledgeReviewReport:
        """返回任务最新的追加式审核报告，不允许调用方按任意文件哈希查询。"""

        statement = text(
            """
            SELECT *
            FROM knowledge_review_reports
            WHERE job_id = :job_id
            ORDER BY report_version DESC
            LIMIT 1
            """
        )
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement, {"job_id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeReviewReportNotFound("未找到知识审查报告")
        return _review_report_from_row(row)

    async def list_review_decisions(self, report_id: str) -> list[KnowledgeReviewDecision]:
        """按创建顺序返回报告的不可变专业审核决定。"""

        statement = text(
            """
            SELECT * FROM knowledge_review_decisions
            WHERE report_id = :report_id
            ORDER BY created_at, id
            """
        )
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, {"report_id": report_id})).mappings().all()
        return [_review_decision_from_row(row) for row in rows]

    async def record_review_decision(
        self,
        decision: KnowledgeReviewDecision,
        report: KnowledgeReviewReport,
    ) -> KnowledgeReviewOutcome:
        """原子保存一个领域决定，并在全部领域通过后只签发一次发布凭证。

        同一报告和领域只有一个终局决定。审核拒绝后必须修订源文档并重新上传，
        不能通过覆盖旧记录消除不利证据。
        """

        if decision.report_id != report.id or decision.job_id != report.job_id:
            raise ValueError("审查决定必须绑定到报告和任务")
        insert_decision = text(
            """
            INSERT INTO knowledge_review_decisions (
                id, report_id, job_id, review_domain, decision, scope_type, page_numbers,
                regions, finding_codes, reviewer_id, reviewer_roles, reviewer_capabilities,
                reviewer_qualifications, reviewer_organization_ids, comment
            ) VALUES (
                :id, :report_id, :job_id, :review_domain, :decision, :scope_type,
                :page_numbers, CAST(:regions AS JSONB), :finding_codes, :reviewer_id,
                :reviewer_roles, :reviewer_capabilities, :reviewer_qualifications,
                :reviewer_organization_ids, :comment
            )
            RETURNING *
            """
        )
        list_decisions = text(
            """
            SELECT * FROM knowledge_review_decisions
            WHERE report_id = :report_id
            ORDER BY created_at, id
            """
        )
        insert_credential = text(
            """
            INSERT INTO knowledge_publication_credentials (
                id, job_id, report_id, report_version, document_sha256,
                parser_pipeline_version, review_policy_version, decision_ids,
                approved_visual_pages
            ) VALUES (
                :id, :job_id, :report_id, :report_version, :document_sha256,
                :parser_pipeline_version, :review_policy_version, :decision_ids,
                :approved_visual_pages
            )
            ON CONFLICT (report_id) DO NOTHING
            RETURNING *
            """
        )
        select_credential = text(
            "SELECT * FROM knowledge_publication_credentials WHERE report_id = :report_id"
        )
        reject_job = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'REJECTED', reviewer_id = :reviewer_id,
                review_comment = :comment, reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id AND status = 'PENDING_REVIEW'
            RETURNING id
            """
        )
        try:
            async with self._database.engine.begin() as connection:
                row = (
                    (await connection.execute(insert_decision, _review_decision_params(decision)))
                    .mappings()
                    .one()
                )
                restored = _review_decision_from_row(row)
                if decision.decision == "REJECTED":
                    rejected = (
                        await connection.execute(
                            reject_job,
                            {
                                "job_id": decision.job_id,
                                "reviewer_id": decision.reviewer_id,
                                "comment": decision.comment[:500],
                            },
                        )
                    ).first()
                    if rejected is None:
                        raise KnowledgeJobTransitionError("知识审查拒绝不适用于当前任务")
                    return KnowledgeReviewOutcome(restored, None)

                rows = (
                    (await connection.execute(list_decisions, {"report_id": report.id}))
                    .mappings()
                    .all()
                )
                decisions = [_review_decision_from_row(item) for item in rows]
                approved_domains = {
                    item.review_domain for item in decisions if item.decision == "APPROVED"
                }
                credential: KnowledgePublicationCredential | None = None
                if approved_domains == set(report.required_review_domains):
                    credential_row = (
                        (
                            await connection.execute(
                                insert_credential,
                                {
                                    "id": token_hex(16),
                                    "job_id": report.job_id,
                                    "report_id": report.id,
                                    "report_version": report.report_version,
                                    "document_sha256": report.document_sha256,
                                    "parser_pipeline_version": report.parser_pipeline_version,
                                    "review_policy_version": report.review_policy_version,
                                    "decision_ids": [item.id for item in decisions],
                                    "approved_visual_pages": list(approved_visual_pages(report)),
                                },
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if credential_row is None:
                        credential_row = (
                            (await connection.execute(select_credential, {"report_id": report.id}))
                            .mappings()
                            .one()
                        )
                    credential = _publication_credential_from_row(credential_row)
                return KnowledgeReviewOutcome(restored, credential)
        except IntegrityError as exc:
            raise KnowledgeJobTransitionError("该审查领域已经存在最终决定") from exc

    async def get_publication_credential(
        self, job_id: str
    ) -> KnowledgePublicationCredential | None:
        """读取任务唯一且未必仍有效的发布凭证；有效性由业务层绑定报告复核。"""

        statement = text("SELECT * FROM knowledge_publication_credentials WHERE job_id = :job_id")
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement, {"job_id": job_id})).mappings().first()
        return _publication_credential_from_row(row) if row is not None else None

    async def get_job(self, job_id: str) -> KnowledgeIngestionJob:
        """返回一个任务；不存在时抛出稳定的领域异常。"""

        statement = text("SELECT * FROM knowledge_ingestion_jobs WHERE id = :id")
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement, {"id": job_id})).mappings().first()
        if row is None:
            raise KnowledgeJobNotFound("未找到知识摄取任务")
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
            raise ValueError("任务列表限制必须在 1 到 100 之间")
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
        )
        if not platform_wide:
            statement = statement.bindparams(bindparam("organization_ids", expanding=True))
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
            raise ValueError("Worker 批次大小必须在 1 到 100 之间")
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
            raise KnowledgeJobTransitionError("知识摄取任务状态转换被拒绝")
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


def _review_report_params(report: KnowledgeReviewReport) -> dict[str, Any]:
    """显式序列化 JSONB 证据，避免数据库驱动隐式改变列表和小数结构。"""

    return {
        "id": report.id,
        "job_id": report.job_id,
        "report_version": report.report_version,
        "document_sha256": report.document_sha256,
        "parser_name": report.parser_name,
        "parser_version": report.parser_version,
        "parser_pipeline_version": report.parser_pipeline_version,
        "review_policy_version": report.review_policy_version,
        "media_type": report.media_type,
        "declared_risk_level": report.declared_risk_level,
        "source_requires_human_review": report.source_requires_human_review,
        "status": report.status,
        "quality_metrics": json.dumps(report.quality_metrics, ensure_ascii=False),
        "page_profiles": json.dumps(
            [profile.as_dict() for profile in report.page_profiles], ensure_ascii=False
        ),
        "warnings": list(report.warnings),
        "findings": json.dumps(
            [finding.as_dict() for finding in report.findings], ensure_ascii=False
        ),
        "required_review_domains": list(report.required_review_domains),
        "recommended_reviewer_roles": list(report.recommended_reviewer_roles),
        "required_qualifications": list(report.required_qualifications),
    }


def _review_report_from_row(row: Any) -> KnowledgeReviewReport:
    """恢复类型化报告；数据库 JSONB 中只读取本服务写入的稳定字段。"""

    page_profiles = tuple(
        PdfPageProfile(
            page_number=int(item["page_number"]),
            image_count=int(item["image_count"]),
            image_area_ratio=float(item["image_area_ratio"]),
            native_text_chars=int(item["native_text_chars"]),
            text_area_ratio=float(item["text_area_ratio"]),
            table_count=int(item["table_count"]),
            caption_count=int(item["caption_count"]),
            route=item["route"],
            reasons=tuple(str(reason) for reason in item.get("reasons", ())),
        )
        for item in row["page_profiles"] or ()
    )
    findings = tuple(
        KnowledgeReviewFinding(
            code=str(item["code"]),
            severity=item["severity"],
            message=str(item["message"]),
            pages=tuple(int(page) for page in item.get("pages", ())),
        )
        for item in row["findings"] or ()
    )
    return KnowledgeReviewReport(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        report_version=int(row["report_version"]),
        document_sha256=str(row["document_sha256"]),
        parser_name=str(row["parser_name"]),
        parser_version=str(row["parser_version"]),
        parser_pipeline_version=str(row["parser_pipeline_version"]),
        review_policy_version=str(row["review_policy_version"]),
        media_type=str(row["media_type"]),
        declared_risk_level=str(row["declared_risk_level"]),
        source_requires_human_review=bool(row["source_requires_human_review"]),
        status=row["status"],
        quality_metrics=dict(row["quality_metrics"] or {}),
        page_profiles=page_profiles,
        warnings=tuple(str(item) for item in row["warnings"] or ()),
        findings=findings,
        required_review_domains=tuple(str(item) for item in row["required_review_domains"] or ()),
        recommended_reviewer_roles=tuple(
            str(item) for item in row["recommended_reviewer_roles"] or ()
        ),
        required_qualifications=tuple(str(item) for item in row["required_qualifications"] or ()),
        created_at=row["created_at"],
    )


def _review_decision_params(decision: KnowledgeReviewDecision) -> dict[str, Any]:
    """序列化审核范围和签名授权快照，不从请求体补充任何权限字段。"""

    return {
        "id": decision.id,
        "report_id": decision.report_id,
        "job_id": decision.job_id,
        "review_domain": decision.review_domain,
        "decision": decision.decision,
        "scope_type": decision.scope_type,
        "page_numbers": list(decision.page_numbers),
        "regions": json.dumps(
            [
                {
                    "page_number": region.page_number,
                    "x": region.x,
                    "y": region.y,
                    "width": region.width,
                    "height": region.height,
                    "label": region.label,
                }
                for region in decision.regions
            ],
            ensure_ascii=False,
        ),
        "finding_codes": list(decision.finding_codes),
        "reviewer_id": decision.reviewer_id,
        "reviewer_roles": list(decision.reviewer_roles),
        "reviewer_capabilities": list(decision.reviewer_capabilities),
        "reviewer_qualifications": list(decision.reviewer_qualifications),
        "reviewer_organization_ids": list(decision.reviewer_organization_ids),
        "comment": decision.comment,
    }


def _review_decision_from_row(row: Any) -> KnowledgeReviewDecision:
    regions = tuple(
        KnowledgeReviewRegion(
            page_number=int(item["page_number"]),
            x=float(item["x"]),
            y=float(item["y"]),
            width=float(item["width"]),
            height=float(item["height"]),
            label=str(item["label"]) if item.get("label") else None,
        )
        for item in row["regions"] or ()
    )
    return KnowledgeReviewDecision(
        id=str(row["id"]),
        report_id=str(row["report_id"]),
        job_id=str(row["job_id"]),
        review_domain=row["review_domain"],
        decision=row["decision"],
        scope_type=row["scope_type"],
        page_numbers=tuple(int(page) for page in row["page_numbers"] or ()),
        regions=regions,
        finding_codes=tuple(str(code) for code in row["finding_codes"] or ()),
        reviewer_id=str(row["reviewer_id"]),
        reviewer_roles=tuple(str(role) for role in row["reviewer_roles"] or ()),
        reviewer_capabilities=tuple(str(value) for value in row["reviewer_capabilities"] or ()),
        reviewer_qualifications=tuple(str(value) for value in row["reviewer_qualifications"] or ()),
        reviewer_organization_ids=tuple(
            str(value) for value in row["reviewer_organization_ids"] or ()
        ),
        comment=str(row["comment"]),
        created_at=row["created_at"],
    )


def _publication_credential_from_row(row: Any) -> KnowledgePublicationCredential:
    return KnowledgePublicationCredential(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        report_id=str(row["report_id"]),
        report_version=int(row["report_version"]),
        document_sha256=str(row["document_sha256"]),
        parser_pipeline_version=str(row["parser_pipeline_version"]),
        review_policy_version=str(row["review_policy_version"]),
        decision_ids=tuple(str(value) for value in row["decision_ids"] or ()),
        approved_visual_pages=tuple(int(page) for page in row["approved_visual_pages"] or ()),
        issued_at=row["issued_at"],
        revoked_at=row["revoked_at"],
    )
