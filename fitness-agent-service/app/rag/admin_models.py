"""Knowledge-base administration domain objects and lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

JobStatus = Literal[
    "PENDING_REVIEW",
    "QUEUED",
    "INDEXING",
    "SUCCEEDED",
    "FAILED",
    "REJECTED",
]
Visibility = Literal["GLOBAL", "ORGANIZATION", "PRIVATE"]


@dataclass(frozen=True)
class KnowledgeIngestionJob:
    """A durable upload/indexing task, separate from the searchable document version."""

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


@dataclass(frozen=True)
class KnowledgeUploadMetadata:
    """Validated metadata supplied alongside an uploaded document."""

    source_uri: str
    title: str
    document_type: str
    organization_id: str | None
    visibility: Visibility
    allowed_roles: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None


class KnowledgeAdminError(RuntimeError):
    """Stable business error for admin workflow transitions."""


class KnowledgeJobNotFound(KnowledgeAdminError):
    """The requested task does not exist."""


class KnowledgeJobTransitionError(KnowledgeAdminError):
    """The requested transition is not valid for the current task state."""


class KnowledgeAdminForbidden(KnowledgeAdminError):
    """The signed identity is not allowed to manage the knowledge base."""


def job_from_row(row: Any) -> KnowledgeIngestionJob:
    """Convert a database mapping into a typed task without exposing SQL rows upstream."""

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
    )
