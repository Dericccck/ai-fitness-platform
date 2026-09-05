"""带审核门禁的知识上传与索引编排。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from secrets import token_hex

from app.infrastructure.agent_context import AgentIdentity

from .admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeJobTransitionError,
    KnowledgeReviewReportNotFound,
    KnowledgeUploadConflict,
    KnowledgeUploadMetadata,
)
from .admin_repository import KnowledgeIngestionRepository
from .formats import DocumentParserRegistry
from .ingestion import DocumentIngestionService, IngestionRequest
from .repository import KnowledgeRepository
from .review import KnowledgeReviewReport, KnowledgeReviewReportBuilder
from .safety import DocumentScanner, StructuralDocumentScanner
from .storage import DocumentStorage

# 前两个名称来自 Java Tool Gateway 的正式 AgentContext；后三个是早期 Agent
# 服务和本地脚本使用的兼容别名。保留别名避免旧任务工具中断，但新集成应只签发正式角色。
ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "ADMIN", "ORG_ADMIN", "SUPER_ADMIN"})
PLATFORM_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ADMIN", "SUPER_ADMIN"})
_SAFE_TEXT = re.compile(r"^[^\r\n]{1,256}$")
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class KnowledgeAdminService:
    """协调授权、暂存、审核状态转换和索引任务。

    该服务有意不让 LLM 参与文档发布。由签名管理员身份提交和审核文件，入库服务负责
    解析、Embedding、父子节点构建和发布。
    """

    def __init__(
        self,
        jobs: KnowledgeIngestionRepository,
        knowledge_repository: KnowledgeRepository,
        ingestion: DocumentIngestionService,
        storage: DocumentStorage,
        parser_registry: DocumentParserRegistry,
        review_report_builder: KnowledgeReviewReportBuilder,
        safety_scanner: DocumentScanner | None = None,
        *,
        max_source_bytes: int,
        max_attempts: int = 3,
    ) -> None:
        self.jobs = jobs
        self.knowledge_repository = knowledge_repository
        self.ingestion = ingestion
        self.storage = storage
        self.parser_registry = parser_registry
        self.review_report_builder = review_report_builder
        self.safety_scanner = safety_scanner or StructuralDocumentScanner()
        self.max_source_bytes = max_source_bytes
        self.max_attempts = max_attempts

    async def submit_upload(
        self,
        identity: AgentIdentity,
        *,
        file_name: str,
        content_type: str | None,
        content: bytes,
        metadata: KnowledgeUploadMetadata,
        idempotency_key: str | None = None,
    ) -> KnowledgeIngestionJob:
        """校验管理员上传，私有暂存文件并创建审核任务。"""

        self.require_admin(identity)
        self._validate_metadata(identity, metadata)
        self._validate_idempotency_key(idempotency_key)
        if not content:
            raise ValueError("上传文档不能为空")
        if len(content) > self.max_source_bytes:
            raise ValueError("上传文档超过配置的大小限制")

        # 作用域必须在查询当前版本和任务去重之前确定。否则同一个 source_uri
        # 可能把不同机构或不同 PRIVATE 所有者误判为同一份文档。
        organization_id = metadata.organization_id
        if metadata.visibility == "ORGANIZATION" and organization_id is None:
            organization_id = next(iter(identity.organization_ids))
        owner_user_id = identity.subject if metadata.visibility == "PRIVATE" else None
        safety_result = self.safety_scanner.scan(file_name, content)

        # 幂等键解决客户端超时重试；同来源锁解决两个不同请求同时上传同一作用域。
        # 两条查询都带完整作用域，避免以任务存在性泄露其他租户信息。
        if idempotency_key is not None:
            existing = await self.jobs.get_job_by_idempotency_key(
                submitted_by=identity.subject,
                idempotency_key=idempotency_key,
                organization_id=organization_id,
                owner_user_id=owner_user_id,
                visibility=metadata.visibility,
            )
            if existing is not None:
                if not self._same_upload(
                    existing,
                    file_name=file_name,
                    content_type=content_type,
                    content_sha256=safety_result.sha256,
                    metadata=metadata,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                ):
                    raise KnowledgeUploadConflict("幂等键已用于另一份上传请求")
                return existing

        active = await self.jobs.get_active_job_by_source(
            metadata.source_uri,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            visibility=metadata.visibility,
        )
        if active is not None:
            if not self._same_upload(
                active,
                file_name=file_name,
                content_type=content_type,
                content_sha256=safety_result.sha256,
                metadata=metadata,
                organization_id=organization_id,
                owner_user_id=owner_user_id,
            ):
                raise KnowledgeUploadConflict("同一来源已有不同内容的上传任务正在处理")
            return active

        # 在信任边界处先解析一次。后续 Worker 会重新解析不可变字节，
        # 避免损坏文件长期停留在审核队列中而无人发现。
        parsed = self.parser_registry.parse(content, file_name=file_name)

        current = await self.knowledge_repository.get_current_document(
            metadata.source_uri,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            visibility=metadata.visibility,
        )
        requested_version = 1 if current is None else current.version + 1
        job_id = token_hex(16)
        report = self.review_report_builder.build(
            report_id=token_hex(16),
            job_id=job_id,
            document_sha256=safety_result.sha256,
            document_type=metadata.document_type,
            risk_level=metadata.risk_level,
            requires_human_review=metadata.requires_human_review,
            parsed=parsed,
        )
        storage_key = await asyncio.to_thread(
            self.storage.store,
            job_id,
            file_name,
            content,
            content_type=content_type or "application/octet-stream",
        )
        job = KnowledgeIngestionJob(
            id=job_id,
            source_uri=metadata.source_uri,
            original_filename=file_name,
            storage_key=storage_key,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            title=metadata.title,
            document_type=metadata.document_type,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            visibility=metadata.visibility,
            allowed_roles=metadata.allowed_roles,
            effective_from=metadata.effective_from,
            effective_to=metadata.effective_to,
            requested_version=requested_version,
            submitted_by=identity.subject,
            status="PENDING_REVIEW",
            attempt_count=0,
            max_attempts=self.max_attempts,
            content_sha256=safety_result.sha256,
            safety_status=safety_result.status,
            scanner_name=safety_result.scanner_name,
            malware_status=safety_result.malware_status,
            malware_scanner=safety_result.malware_scanner,
            malware_signature=safety_result.malware_signature,
            malware_scanned_at=safety_result.malware_scanned_at,
            idempotency_key=idempotency_key,
        )
        return await self.jobs.create_job(job=job, review_report=report)

    async def approve(
        self, identity: AgentIdentity, job_id: str, *, comment: str | None = None
    ) -> KnowledgeIngestionJob:
        """批准待审核任务；路由会在本次调用后调度实际 Worker。"""

        self.require_admin(identity)
        job = await self._get_scoped_job(identity, job_id)
        try:
            report = await self.jobs.get_latest_review_report(job_id)
        except KnowledgeReviewReportNotFound as exc:
            raise KnowledgeJobTransitionError("缺少知识审查报告；需要重新分析") from exc
        credential = await self.jobs.get_publication_credential(job.id)
        professionally_approved = credential is not None and credential.validates(report, job)
        if not report.can_admin_approve and not professionally_approved:
            # 这里故意不接受 force/override 参数。BLOCKED 需要重新解析或 OCR，
            # REVIEW_REQUIRED 需要后续专业审核决策；普通管理员备注不能代替二者。
            raise KnowledgeJobTransitionError(
                "知识审查报告不允许管理员批准："
                f"status={report.status}, required_domains={list(report.required_review_domains)}"
            )
        if report.document_sha256 != job.content_sha256:
            raise KnowledgeJobTransitionError("知识审查报告未绑定到暂存文档哈希")
        return await self.jobs.approve(job_id, reviewer_id=identity.subject, comment=comment)

    async def reject(
        self, identity: AgentIdentity, job_id: str, *, comment: str
    ) -> KnowledgeIngestionJob:
        """拒绝任务，同时保留证据和审核人决定。"""

        self.require_admin(identity)
        if not comment.strip():
            raise ValueError("必须填写拒绝评论")
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.reject(job_id, reviewer_id=identity.subject, comment=comment[:500])

    async def retry(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """在有限重试预算内手动重新排队失败任务。"""

        self.require_admin(identity)
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.retry(job_id, reviewer_id=identity.subject)

    async def get_job(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """只向管理员返回任务状态；这里绝不返回文档内容。"""

        self.require_admin(identity)
        return await self._get_scoped_job(identity, job_id)

    async def get_review_report(
        self, identity: AgentIdentity, job_id: str
    ) -> KnowledgeReviewReport:
        """在任务范围校验后返回报告，避免跨组织枚举页码和审核要求。"""

        self.require_admin(identity)
        await self._get_scoped_job(identity, job_id)
        return await self.jobs.get_latest_review_report(job_id)

    async def get_review_report_status(
        self, identity: AgentIdentity, job_id: str
    ) -> tuple[KnowledgeReviewReport, bool]:
        """返回报告和实时发布就绪状态，避免 API 在专业审核后仍显示不可批准。"""

        job = await self._get_scoped_job(identity, job_id)
        report = await self.jobs.get_latest_review_report(job_id)
        credential = await self.jobs.get_publication_credential(job_id)
        approval_ready = report.can_admin_approve or (
            credential is not None and credential.validates(report, job)
        )
        return report, approval_ready

    async def list_jobs(
        self, identity: AgentIdentity, *, limit: int = 50
    ) -> list[KnowledgeIngestionJob]:
        """返回数量受限的任务摘要，供管理员看板使用。"""

        self.require_admin(identity)
        return await self.jobs.list_jobs(
            organization_ids=identity.organization_ids,
            platform_wide=bool(PLATFORM_ADMIN_ROLES.intersection(identity.roles)),
            limit=limit,
        )

    async def process_job(self, job_id: str) -> None:
        """执行一个排队任务并持久化成功/失败状态，不泄露原始文档文本。"""

        job = await self.jobs.claim(job_id)
        if job is None:
            return
        try:
            content = await asyncio.to_thread(self.storage.read, job.storage_key)
            report = await self.jobs.get_latest_review_report(job.id)
            actual_sha256 = hashlib.sha256(content).hexdigest()
            credential = await self.jobs.get_publication_credential(job.id)
            professionally_approved = credential is not None and credential.validates(report, job)
            if (
                (not report.can_admin_approve and not professionally_approved)
                or report.document_sha256 != job.content_sha256
                or actual_sha256 != job.content_sha256
            ):
                # 审批到执行之间可能发生部署升级或对象篡改。Worker 必须再次校验
                # 报告版本和内容身份，不能因为任务已经排队就默认信任旧决定。
                raise KnowledgeJobTransitionError("审查报告已过期或暂存文档哈希验证失败")
            ingestion_request = IngestionRequest(
                source_uri=job.source_uri,
                title=job.title,
                document_type=job.document_type,
                raw_content="",
                organization_id=job.organization_id,
                owner_user_id=job.owner_user_id,
                visibility=job.visibility,
                allowed_roles=job.allowed_roles,
                version=job.requested_version,
                effective_from=job.effective_from,
                effective_to=job.effective_to,
            )
            if professionally_approved and credential is not None:
                result = await self.ingestion.ingest_file(
                    ingestion_request,
                    file_name=job.original_filename,
                    content=content,
                    reviewed_visual_pages=credential.approved_visual_pages,
                )
            else:
                result = await self.ingestion.ingest_file(
                    ingestion_request,
                    file_name=job.original_filename,
                    content=content,
                )
            await self.jobs.complete(job_id, document_id=result.document_id)
        except Exception as exc:  # noqa: BLE001 - Worker 必须持久化稳定的失败状态
            await self.jobs.fail(
                job_id,
                error_code=type(exc).__name__,
                error_message=str(exc) or "索引任务失败",
            )

    @staticmethod
    def require_admin(identity: AgentIdentity) -> None:
        """从签名上下文校验管理员角色，绝不信任 multipart 表单中的角色。"""

        if not ADMIN_ROLES.intersection(identity.roles):
            raise KnowledgeAdminForbidden("需要管理员角色")

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str | None) -> None:
        if idempotency_key is not None and not _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError(
                "Idempotency-Key 只能包含字母、数字、点、下划线、冒号或连字符，长度 1-128"
            )

    @staticmethod
    def _same_upload(
        job: KnowledgeIngestionJob,
        *,
        file_name: str,
        content_type: str | None,
        content_sha256: str,
        metadata: KnowledgeUploadMetadata,
        organization_id: str | None,
        owner_user_id: str | None,
    ) -> bool:
        """比较可证明的请求身份，不比较任务状态或自动分配的版本号。"""

        return (
            job.source_uri == metadata.source_uri
            and job.original_filename == file_name
            and job.content_type == (content_type or "application/octet-stream")
            and job.content_sha256 == content_sha256
            and job.title == metadata.title
            and job.document_type == metadata.document_type
            and job.organization_id == organization_id
            and job.owner_user_id == owner_user_id
            and job.visibility == metadata.visibility
            and job.allowed_roles == metadata.allowed_roles
            and job.effective_from == metadata.effective_from
            and job.effective_to == metadata.effective_to
        )

    async def _get_scoped_job(self, identity: AgentIdentity, job_id: str) -> KnowledgeIngestionJob:
        """在返回或转换任务前应用任务范围校验。"""

        job = await self.jobs.get_job(job_id)
        if PLATFORM_ADMIN_ROLES.intersection(identity.roles):
            return job
        if (
            job.visibility == "ORGANIZATION"
            and job.organization_id is not None
            and job.organization_id in identity.organization_ids
        ):
            return job
        raise KnowledgeAdminForbidden("知识任务不在已签名管理员权限范围内")

    @staticmethod
    def _validate_metadata(identity: AgentIdentity, metadata: KnowledgeUploadMetadata) -> None:
        if not _SAFE_TEXT.fullmatch(metadata.source_uri) or not metadata.source_uri.startswith(
            "knowledge://"
        ):
            raise ValueError("source_uri 必须是安全的 knowledge:// URI")
        if not _SAFE_TEXT.fullmatch(metadata.title):
            raise ValueError("标题无效")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", metadata.document_type):
            raise ValueError("document_type 必须为大写且长度受限")
        if metadata.risk_level not in {"NORMAL", "CAUTION", "MEDICAL"}:
            raise ValueError("risk_level 必须为 NORMAL、CAUTION 或 MEDICAL")
        if metadata.visibility == "GLOBAL" and metadata.organization_id is not None:
            raise ValueError("全局文档不能携带机构范围")
        if metadata.visibility == "GLOBAL" and not PLATFORM_ADMIN_ROLES.intersection(
            identity.roles
        ):
            raise KnowledgeAdminForbidden("全局知识需要平台管理员")
        if metadata.visibility == "ORGANIZATION":
            if metadata.organization_id is None:
                if len(identity.organization_ids) != 1:
                    raise ValueError("多机构管理员必须提供 organization_id")
            elif metadata.organization_id not in identity.organization_ids:
                raise KnowledgeAdminForbidden("机构不在已签名管理员权限范围内")
        if metadata.effective_to is not None and metadata.effective_to <= metadata.effective_from:
            raise ValueError("effective_to 必须晚于 effective_from")
