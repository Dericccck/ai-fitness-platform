"""知识库管理领域对象和生命周期状态。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

# 知识上传任务状态：PENDING_REVIEW 待审核；QUEUED 已批准、等待 Worker；
# INDEXING Worker 正在解析/Embedding/发布；SUCCEEDED 已完成；FAILED 处理失败；
# REJECTED 被管理员或专业审核拒绝。FAILED 只有在重试预算内才能回到 QUEUED。
JobStatus = Literal["PENDING_REVIEW", "QUEUED", "INDEXING", "SUCCEEDED", "FAILED", "REJECTED"]

# 可见范围不是流程状态，而是每次 SQL 检索必须使用的权限维度：GLOBAL 全局、
# ORGANIZATION 机构内、PRIVATE 仅文档所有者。
Visibility = Literal["GLOBAL", "ORGANIZATION", "PRIVATE"]

# 重建批次：QUEUED 排队；INDEXING 执行中；SUCCEEDED 全部完成；FAILED 批次失败。
ReindexJobStatus = Literal["QUEUED", "INDEXING", "SUCCEEDED", "FAILED"]

# 重建明细：PENDING 待领取；INDEXING 已领取；SUCCEEDED 成功；SKIPPED 内容未变化而跳过；
# FAILED 该文档处理失败。批次可以成功，但必须保留失败明细供人工处理。
ReindexItemStatus = Literal["PENDING", "INDEXING", "SUCCEEDED", "SKIPPED", "FAILED"]


@dataclass(frozen=True)
class KnowledgeIngestionJob:
    """持久化的上传/索引任务，与可检索文档版本分离。"""

    id: str
    source_uri: str
    original_filename: str
    storage_key: str
    content_type: str
    size_bytes: int
    title: str
    document_type: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: Visibility
    allowed_roles: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None
    requested_version: int
    submitted_by: str
    status: JobStatus
    attempt_count: int
    max_attempts: int
    reviewer_id: str | None = None
    review_comment: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    document_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    reviewed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    content_sha256: str = ""
    safety_status: str = "STRUCTURAL_VALIDATED"
    scanner_name: str = "structural-v1"
    malware_status: str = "NOT_CONFIGURED"
    malware_scanner: str = "not-configured"
    malware_signature: str | None = None
    malware_scanned_at: datetime | None = None
    # 客户端重试令牌：同一提交者和同一知识作用域内必须只对应一个任务。
    idempotency_key: str | None = None


@dataclass(frozen=True)
class KnowledgeUploadMetadata:
    """随上传文档一同提交并经过校验的元数据。"""

    source_uri: str
    title: str
    document_type: str
    organization_id: str | None
    visibility: Visibility
    allowed_roles: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None
    risk_level: str = "NORMAL"
    requires_human_review: bool = False


@dataclass(frozen=True)
class KnowledgeReindexSource:
    """一个索引重建项目使用的不可变来源快照。

    可检索文档表只保存规范化知识，不保存原始二进制。创建重建任务时必须快照暂存
    对象键和原发布视觉审核页，使长任务可复现，且不受同一来源后续上传版本影响。
    """

    document_id: str
    source_uri: str
    title: str
    document_type: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: Visibility
    allowed_roles: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None
    version: int
    storage_key: str
    original_filename: str
    content_type: str
    approved_visual_pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class KnowledgeReindexJob:
    """用于重建已发布知识文档的持久化批次任务。"""

    id: str
    requested_by: str
    organization_id: str | None
    target_document_id: str | None
    status: ReindexJobStatus
    total_documents: int
    processed_documents: int
    succeeded_documents: int
    skipped_documents: int
    failed_documents: int
    attempt_count: int
    max_attempts: int
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class KnowledgeReindexItem:
    """索引重建批次中的一个文档级项目。"""

    id: str
    job_id: str
    document_id: str
    source_uri: str
    title: str
    document_type: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: Visibility
    allowed_roles: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None
    version: int
    storage_key: str
    original_filename: str
    content_type: str
    status: ReindexItemStatus
    attempt_count: int
    max_attempts: int
    approved_visual_pages: tuple[int, ...] = ()
    error_message: str | None = None


class KnowledgeAdminError(RuntimeError):
    """管理员工作流状态转换使用的稳定业务异常。"""


class KnowledgeJobNotFound(KnowledgeAdminError):
    """请求的任务不存在。"""


class KnowledgeJobTransitionError(KnowledgeAdminError):
    """请求的状态转换不符合当前任务状态。"""


class KnowledgeAdminForbidden(KnowledgeAdminError):
    """签名身份没有管理知识库的权限。"""


class KnowledgeReindexNotFound(KnowledgeAdminError):
    """请求的重建范围内没有可用的已发布文档。"""


class KnowledgeReviewReportNotFound(KnowledgeAdminError):
    """上传任务尚未生成可审计的解析审核报告。"""


class KnowledgeUploadConflict(KnowledgeAdminError):
    """上传与已有幂等任务或进行中的同来源任务不一致。"""


def job_from_row(row: Any) -> KnowledgeIngestionJob:
    """将数据库映射转换为类型化任务，不向上层暴露 SQL 行。"""

    return KnowledgeIngestionJob(
        id=str(row["id"]),
        source_uri=str(row["source_uri"]),
        original_filename=str(row["original_filename"]),
        storage_key=str(row["storage_key"]),
        content_type=str(row["content_type"]),
        size_bytes=int(row["size_bytes"]),
        title=str(row["title"]),
        document_type=str(row["document_type"]),
        organization_id=str(row["organization_id"]) if row["organization_id"] else None,
        owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] else None,
        visibility=row["visibility"],
        allowed_roles=tuple(row["allowed_roles"] or ()),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        requested_version=int(row["requested_version"]),
        submitted_by=str(row["submitted_by"]),
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        reviewer_id=str(row["reviewer_id"]) if row["reviewer_id"] else None,
        review_comment=str(row["review_comment"]) if row["review_comment"] else None,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        error_message=str(row["error_message"]) if row["error_message"] else None,
        document_id=str(row["document_id"]) if row["document_id"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reviewed_at=row["reviewed_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        content_sha256=str(row["content_sha256"] or ""),
        safety_status=str(row["safety_status"] or "UNKNOWN"),
        scanner_name=str(row["scanner_name"] or "unknown"),
        malware_status=str(row.get("malware_status") or "UNKNOWN"),
        malware_scanner=str(row.get("malware_scanner") or "unknown"),
        malware_signature=str(row["malware_signature"]) if row.get("malware_signature") else None,
        malware_scanned_at=row.get("malware_scanned_at"),
        idempotency_key=(str(row["idempotency_key"]) if row.get("idempotency_key") else None),
    )


def reindex_job_from_row(row: Any) -> KnowledgeReindexJob:
    """将索引重建批次数据行转换为稳定领域对象。"""

    return KnowledgeReindexJob(
        id=str(row["id"]),
        requested_by=str(row["requested_by"]),
        organization_id=str(row["organization_id"]) if row["organization_id"] else None,
        target_document_id=(str(row["target_document_id"]) if row["target_document_id"] else None),
        status=row["status"],
        total_documents=int(row["total_documents"]),
        processed_documents=int(row["processed_documents"]),
        succeeded_documents=int(row["succeeded_documents"]),
        skipped_documents=int(row["skipped_documents"]),
        failed_documents=int(row["failed_documents"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        error_message=str(row["error_message"]) if row["error_message"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def reindex_item_from_row(row: Any) -> KnowledgeReindexItem:
    """将文档级索引重建数据行转换为对象，不向上层泄露 SQL 映射。"""

    return KnowledgeReindexItem(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        document_id=str(row["document_id"]),
        source_uri=str(row["source_uri"]),
        title=str(row["title"]),
        document_type=str(row["document_type"]),
        organization_id=str(row["organization_id"]) if row["organization_id"] else None,
        owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] else None,
        visibility=row["visibility"],
        allowed_roles=tuple(row["allowed_roles"] or ()),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        version=int(row["version"]),
        storage_key=str(row["storage_key"]),
        original_filename=str(row["original_filename"]),
        content_type=str(row["content_type"]),
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        approved_visual_pages=tuple(int(page) for page in row["approved_visual_pages"] or ()),
        error_message=str(row["error_message"]) if row["error_message"] else None,
    )
